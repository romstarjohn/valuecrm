from typing import List, Dict, Any, Optional
from shared.constants import MAX_BULK_ENROLLMENT_SIZE
from shared.logging_utils import get_logger, log_service_start, log_service_success, log_service_failure
from apps.contacts.services import ContactService
from apps.courses.models import Course
from integrations.clickfunnels.client import ClickFunnelsClient
from .models import EnrollmentAttempt
from .schemas import EnrollmentResultDTO, SuspensionResultDTO

logger = get_logger(__name__)

class EnrollmentService:
    def __init__(self, client: ClickFunnelsClient, contact_service: ContactService):
        self.client = client
        self.contact_service = contact_service

    def enroll_contact(self, workspace_subdomain: str, workspace_id: int, email: str, cf_course_id: str) -> EnrollmentResultDTO:
        log_service_start(
            logger, "EnrollmentService", "enroll_contact", 
            workspace_id=workspace_id, course_id=cf_course_id
        )
        
        # 1. Validate course exists locally
        # Note: Course model uses 'workspace_id' to store the subdomain in current context, 
        # but the contract says we should ideally use the numeric ID. 
        # For now, searching by cf_course_id is the primary lookup.
        course = Course.objects.filter(cf_course_id=cf_course_id).first()
        if not course:
            error_msg = f"Course {cf_course_id} not found."
            log_service_failure(
                logger, "EnrollmentService", "enroll_contact", 
                ValueError(error_msg), workspace_id=workspace_id, course_id=cf_course_id
            )
            return EnrollmentResultDTO(
                contact_email=email,
                course_id=cf_course_id,
                status=EnrollmentAttempt.Status.FAILURE,
                error_message=error_msg,
                error_log=error_msg
            )

        contact = None
        try:
            # 2. Get/Create remote contact (ensures local record too)
            contact = self.contact_service.get_or_create_remote_contact(
                subdomain=workspace_subdomain,
                workspace_id=workspace_id,
                contact_data={"email": email}
            )

            # 3. Enroll contact in ClickFunnels
            enrollment_dto = self.client.enroll_contact_in_course(
                subdomain=workspace_subdomain,
                contact_id=int(contact.cf_contact_id),
                course_id=int(cf_course_id)
            )

            # 4. Create Success EnrollmentAttempt
            attempt = EnrollmentAttempt.objects.create(
                contact=contact,
                course=course,
                status=EnrollmentAttempt.Status.SUCCESS,
                cf_enrollment_id=str(enrollment_dto.id),
                response_payload=enrollment_dto.model_dump()
            )

            log_service_success(
                logger, "EnrollmentService", "enroll_contact", 
                workspace_id=workspace_id, course_id=cf_course_id, 
                contact_id=contact.id, enrollment_attempt_id=attempt.id
            )
            return EnrollmentResultDTO(
                contact_email=email,
                course_id=cf_course_id,
                status=EnrollmentAttempt.Status.SUCCESS,
                enrollment_attempt_id=attempt.id,
                cf_enrollment_id=attempt.cf_enrollment_id
            )

        except Exception as e:
            # 5. Persist failure if possible
            if not contact:
                contact = self.contact_service.get_by_email(email)
            
            attempt_id = None
            if contact and course:
                attempt = EnrollmentAttempt.objects.create(
                    contact=contact,
                    course=course,
                    status=EnrollmentAttempt.Status.FAILURE,
                    error_log=str(e),
                    response_payload=getattr(e, 'response_body', {}) if hasattr(e, 'response_body') else {}
                )
                attempt_id = attempt.id

            log_service_failure(
                logger, "EnrollmentService", "enroll_contact", e, 
                workspace_id=workspace_id, course_id=cf_course_id, 
                contact_id=contact.id if contact else None, 
                enrollment_attempt_id=attempt_id
            )
            
            return EnrollmentResultDTO(
                contact_email=email,
                course_id=cf_course_id,
                status=EnrollmentAttempt.Status.FAILURE,
                enrollment_attempt_id=attempt_id,
                error_message=str(e),
                error_log=str(e)
            )

    def set_enrollment_suspension(
        self, workspace_subdomain: str, cf_enrollment_id: str, suspended: bool, reason: str = "",
    ) -> SuspensionResultDTO:
        """
        Pure ClickFunnels call — no Order/ProvisioningRequest knowledge, mirrors
        enroll_contact's separation of concerns. Unlike enroll_contact, this lets
        provider errors propagate rather than swallowing them into a DTO: there's
        no retry loop consuming this (freeze/resume is a single operator-triggered
        attempt), so the caller (EnrollmentAdministrationService) classifies any
        failure once, for its own audit log.
        """
        log_service_start(
            logger, "EnrollmentService", "set_enrollment_suspension",
            cf_enrollment_id=cf_enrollment_id, suspended=suspended,
        )
        dto = self.client.update_enrollment_suspension(
            subdomain=workspace_subdomain,
            enrollment_id=int(cf_enrollment_id),
            suspended=suspended,
            suspension_reason=reason,
        )
        log_service_success(
            logger, "EnrollmentService", "set_enrollment_suspension",
            cf_enrollment_id=cf_enrollment_id, suspended=suspended,
        )
        return SuspensionResultDTO(cf_enrollment_id=str(dto.id), suspended=suspended)

    def bulk_enroll(self, workspace_subdomain: str, workspace_id: int, emails: List[str], cf_course_id: str) -> Dict[str, Any]:
        log_service_start(
            logger, "EnrollmentService", "bulk_enroll", 
            workspace_id=workspace_id, course_id=cf_course_id, count=len(emails)
        )
        
        if len(emails) > MAX_BULK_ENROLLMENT_SIZE:
            error_msg = f"Bulk enrollment exceeds maximum size of {MAX_BULK_ENROLLMENT_SIZE}"
            log_service_failure(
                logger, "EnrollmentService", "bulk_enroll", 
                ValueError(error_msg), workspace_id=workspace_id, course_id=cf_course_id
            )
            raise ValueError(error_msg)

        results = []
        success_count = 0
        failure_count = 0

        for email in emails:
            try:
                result_dto = self.enroll_contact(workspace_subdomain, workspace_id, email, cf_course_id)
                results.append(result_dto)
                if result_dto.status == EnrollmentAttempt.Status.SUCCESS:
                    success_count += 1
                else:
                    failure_count += 1
            except Exception as e:
                failure_count += 1
                results.append(EnrollmentResultDTO(
                    contact_email=email,
                    course_id=cf_course_id,
                    status=EnrollmentAttempt.Status.FAILURE,
                    error_message=f"Unexpected Error: {str(e)}",
                    error_log=str(e)
                ))

        log_service_success(
            logger, "EnrollmentService", "bulk_enroll", 
            workspace_id=workspace_id, course_id=cf_course_id, 
            total=len(emails), success=success_count
        )
        
        return {
            "total": len(emails),
            "success_count": success_count,
            "failure_count": failure_count,
            "results": results
        }
