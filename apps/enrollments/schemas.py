from typing import List, Optional
from ninja import Schema

class EnrollmentRequestSchema(Schema):
    email: str
    course_id: str  # cf_course_id

class BulkEnrollmentRequestSchema(Schema):
    emails: List[str]
    course_id: str  # cf_course_id

class EnrollmentResultDTO(Schema):
    contact_email: str
    course_id: str
    status: str
    enrollment_attempt_id: Optional[int] = None
    cf_enrollment_id: Optional[str] = None
    error_message: Optional[str] = None
    error_log: Optional[str] = None

class EnrollmentResponseSchema(EnrollmentResultDTO):
    pass

class BulkEnrollmentResponseSchema(Schema):
    total: int
    success_count: int
    failure_count: int
    results: List[EnrollmentResultDTO]

class SuspensionResultDTO(Schema):
    cf_enrollment_id: str
    suspended: bool
