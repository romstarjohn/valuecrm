from typing import List, Optional, Dict, Any
from shared.logging_utils import get_logger, log_service_start, log_service_success, log_service_failure
from .models import Course
from integrations.clickfunnels.client import ClickFunnelsClient

logger = get_logger(__name__)

class CourseService:
    def __init__(self, client: Optional[ClickFunnelsClient] = None):
        self.client = client

    def list_courses(self) -> List[Course]:
        return Course.objects.all().order_by("name")

    def get_course(self, cf_course_id: str) -> Optional[Course]:
        return Course.objects.filter(cf_course_id=cf_course_id).first()

    def sync_courses(self, workspace_id: int, workspace_subdomain: str) -> Dict[str, int]:
        log_service_start(logger, "CourseService", "sync_courses", workspace_id=workspace_id)
        
        if not self.client:
            raise ValueError("ClickFunnelsClient is required for sync_courses")

        try:
            # Verified signature: list_courses(workspace_subdomain, workspace_id)
            course_dtos = self.client.list_courses(workspace_subdomain, workspace_id)
            
            synced_count = 0
            created_count = 0
            updated_count = 0

            for dto in course_dtos:
                # Use int ID from DTO and convert to string for local DB
                # ClickFunnels Course object uses 'title' and 'description'
                course, created = Course.objects.update_or_create(
                    cf_course_id=str(dto.id),
                    defaults={
                        "workspace_id": workspace_subdomain,
                        "name": dto.title,
                        "description": dto.description,
                        "raw_payload": dto.model_dump(mode="json")
                    }
                )
                synced_count += 1
                if created:
                    created_count += 1
                else:
                    updated_count += 1

            log_service_success(
                logger, "CourseService", "sync_courses", 
                workspace_id=workspace_id, 
                synced_count=synced_count
            )
            
            return {
                "synced_count": synced_count,
                "created_count": created_count,
                "updated_count": updated_count
            }
            
        except Exception as e:
            log_service_failure(logger, "CourseService", "sync_courses", e, workspace_id=workspace_id)
            raise e
