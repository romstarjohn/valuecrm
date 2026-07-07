from typing import List
from ninja import Router
from ninja.responses import Response
from django.shortcuts import get_object_or_404
from .models import Course
from .schemas import CourseSchema, SyncCoursesResponseSchema
from .services import CourseService
from apps.configuration.services import ConfigurationService
from integrations.clickfunnels.client import ClickFunnelsClient

router = Router(tags=["Courses"])

@router.get("/", response=List[CourseSchema])
def list_courses(request):
    service = CourseService()
    return service.list_courses()

@router.post("/sync", response={200: SyncCoursesResponseSchema, 404: dict, 400: dict})
def sync_courses(request):
    """
    API endpoint to trigger course sync.
    Uses active configuration by default.
    """
    config_service = ConfigurationService()
    config = config_service.get_active_config()
    if not config:
        return Response({"message": "No active configuration found"}, status=404)
    
    if not config.workspace_id:
        return Response({"message": "Select a workspace before syncing courses."}, status=400)

    if not config.workspace_subdomain:
        return Response({"message": "Workspace subdomain is missing. Workspace-level API calls cannot continue."}, status=400)

    try:
        client = ClickFunnelsClient.from_configuration(config)
        service = CourseService(client=client)
        result = service.sync_courses(
            workspace_id=int(config.workspace_id),
            workspace_subdomain=config.workspace_subdomain
        )
        return result
    except Exception as e:
        return Response({"message": str(e)}, status=500)

@router.get("/{cf_course_id}", response=CourseSchema)
def get_course(request, cf_course_id: str):
    return get_object_or_404(Course, cf_course_id=cf_course_id)
