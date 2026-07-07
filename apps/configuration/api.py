from ninja import Router
from ninja.responses import Response
from django.shortcuts import get_object_or_404
from .models import ClickFunnelsConfig
from .schemas import ConfigResponseSchema
from .services import ConfigurationService
from integrations.clickfunnels.client import ClickFunnelsClient

router = Router(tags=["Configuration"])

@router.get("/active", response={200: ConfigResponseSchema, 404: dict})
def get_active_configuration(request):
    service = ConfigurationService()
    config = service.get_active_config()
    if not config:
        return Response({"message": "No active configuration found"}, status=404)
    return config

@router.post("/{config_id}/verify", response={200: ConfigResponseSchema, 404: dict})
def verify_configuration(request, config_id: int):
    """
    API version of the full verification flow.
    """
    config = get_object_or_404(ClickFunnelsConfig, id=config_id)
    client = ClickFunnelsClient.from_configuration(config)
    service = ConfigurationService(client=client)
    
    try:
        service.verify_token(config)
        service.fetch_teams(config)
        # If a team is already set, try fetching workspaces too
        if config.team_id:
            service.fetch_workspaces(config, int(config.team_id))
        return config
    except Exception as e:
        return Response({"message": str(e)}, status=400)
