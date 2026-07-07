from typing import Optional, List, Dict, Any
from shared.logging_utils import get_logger, log_service_start, log_service_success, log_service_failure
from shared.security import encrypt_value, decrypt_value
from .models import ClickFunnelsConfig
from integrations.clickfunnels.client import ClickFunnelsClient

logger = get_logger(__name__)

class ConfigurationService:
    def __init__(self, client: Optional[ClickFunnelsClient] = None):
        self.client = client

    def update_credentials(self, config: ClickFunnelsConfig, api_access_token: str):
        """Encrypts and stores the API Access Token."""
        if api_access_token:
            config.api_access_token = encrypt_value(api_access_token)
        config.save()

    def verify_token(self, config: ClickFunnelsConfig) -> bool:
        """Step 1: Check token validity."""
        log_service_start(logger, "ConfigurationService", "verify_token", config_id=config.id)
        try:
            self.client.validate_credentials()
            config.validation_status = ClickFunnelsConfig.ValidationStatus.VALID
            config.save()
            log_service_success(logger, "ConfigurationService", "verify_token", config_id=config.id)
            return True
        except Exception as e:
            config.validation_status = ClickFunnelsConfig.ValidationStatus.INVALID
            config.save()
            log_service_failure(logger, "ConfigurationService", "verify_token", e, config_id=config.id)
            # Re-raise with specific message if it's an auth error
            if getattr(e, "status_code", None) == 401:
                raise Exception("The API access token is invalid or unauthorized.")
            raise e

    def fetch_teams(self, config: ClickFunnelsConfig) -> List[Dict[str, Any]]:
        """Step 2: Fetch available team options."""
        log_service_start(logger, "ConfigurationService", "fetch_teams", config_id=config.id)
        try:
            teams_dto = self.client.list_teams()
            teams_data = [t.model_dump() for t in teams_dto]
            config.raw_payload["available_teams"] = teams_data
            config.save()
            log_service_success(logger, "ConfigurationService", "fetch_teams", count=len(teams_data))
            return teams_data
        except Exception as e:
            log_service_failure(logger, "ConfigurationService", "fetch_teams", e)
            raise Exception("Teams were returned by ClickFunnels, but the response format could not be parsed.") from e

    def select_team(self, config: ClickFunnelsConfig, team_id: int):
        """Action: Store selected team and clear old workspace."""
        teams = config.raw_payload.get("available_teams", [])
        # team_id in DTO is int, but from form it might be str
        team_id = int(team_id)
        team_data = next((t for t in teams if t["id"] == team_id), None)
        
        config.team_id = str(team_id)
        config.team_name = team_data["name"] if team_data else "Unknown Team"
        config.workspace_id = None
        config.workspace_name = None
        config.workspace_subdomain = None
        config.save()

    def fetch_workspaces(self, config: ClickFunnelsConfig, team_id: int) -> List[Dict[str, Any]]:
        """Step 3: Fetch workspaces for selected team."""
        log_service_start(logger, "ConfigurationService", "fetch_workspaces", team_id=team_id)
        try:
            workspaces_dto = self.client.list_workspaces(team_id)
            workspaces_data = [w.model_dump() for w in workspaces_dto]
            config.raw_payload["available_workspaces"] = workspaces_data
            config.save()
            log_service_success(logger, "ConfigurationService", "fetch_workspaces", count=len(workspaces_data))
            return workspaces_data
        except Exception as e:
            log_service_failure(logger, "ConfigurationService", "fetch_workspaces", e)
            raise Exception("Workspaces were returned by ClickFunnels, but the response format could not be parsed.") from e

    def select_workspace(self, config: ClickFunnelsConfig, workspace_id: int):
        """Action: Finalize workspace selection."""
        workspaces = config.raw_payload.get("available_workspaces", [])
        workspace_id = int(workspace_id)
        ws_data = next((w for w in workspaces if w["id"] == workspace_id), None)
        
        if not ws_data:
            raise ValueError("Select a workspace before syncing courses or enrolling contacts.")

        subdomain = ws_data.get("subdomain")
        if not subdomain:
            raise Exception("Workspace subdomain was not returned by ClickFunnels. Cannot make workspace-level API calls.")

        config.workspace_id = str(workspace_id)
        config.workspace_name = ws_data["name"]
        config.workspace_subdomain = subdomain
        config.save()

    def get_active_config(self) -> Optional[ClickFunnelsConfig]:
        return ClickFunnelsConfig.objects.filter(is_active=True).first()
