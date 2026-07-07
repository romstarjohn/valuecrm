from typing import List
from ninja import Router
from ninja.responses import Response
from django.shortcuts import get_object_or_404
from .models import EnrollmentAttempt
from .schemas import (
    EnrollmentRequestSchema, 
    EnrollmentResponseSchema, 
    BulkEnrollmentRequestSchema, 
    BulkEnrollmentResponseSchema
)
from .services import EnrollmentService
from apps.configuration.services import ConfigurationService
from apps.contacts.services import ContactService
from integrations.clickfunnels.client import ClickFunnelsClient

router = Router(tags=["Enrollments"])

def _get_enrollment_service():
    config_service = ConfigurationService()
    config = config_service.get_active_config()
    if not config:
        return None, None
    
    client = ClickFunnelsClient.from_configuration(config)
    contact_service = ContactService(client=client)
    return EnrollmentService(client=client, contact_service=contact_service), config

@router.post("/", response={200: EnrollmentResponseSchema, 404: dict, 400: dict})
def enroll_contact(request, data: EnrollmentRequestSchema):
    service_result = _get_enrollment_service()
    if not service_result:
        return Response({"message": "No active configuration found"}, status=404)
    
    service, config = service_result

    if not config.workspace_id or not config.workspace_subdomain:
        return Response({"message": "Select a workspace in settings before enrolling contacts."}, status=400)
    
    try:
        result_dto = service.enroll_contact(
            workspace_subdomain=config.workspace_subdomain,
            workspace_id=int(config.workspace_id),
            email=data.email, 
            cf_course_id=data.course_id
        )
        return result_dto
    except ValueError as e:
        return Response({"message": str(e)}, status=400)
    except Exception as e:
        return Response({"message": str(e)}, status=500)

@router.post("/bulk", response={200: BulkEnrollmentResponseSchema, 404: dict, 400: dict})
def bulk_enroll_contacts(request, data: BulkEnrollmentRequestSchema):
    service_result = _get_enrollment_service()
    if not service_result:
        return Response({"message": "No active configuration found"}, status=404)
    
    service, config = service_result

    if not config.workspace_id or not config.workspace_subdomain:
        return Response({"message": "Select a workspace in settings before enrolling contacts."}, status=400)
    
    try:
        result = service.bulk_enroll(
            workspace_subdomain=config.workspace_subdomain,
            workspace_id=int(config.workspace_id),
            emails=data.emails, 
            cf_course_id=data.course_id
        )
        return result
    except ValueError as e:
        return Response({"message": str(e)}, status=400)
    except Exception as e:
        return Response({"message": str(e)}, status=500)
