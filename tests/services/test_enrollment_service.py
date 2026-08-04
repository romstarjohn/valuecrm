import pytest
from unittest.mock import MagicMock
from apps.enrollments.models import EnrollmentAttempt
from apps.enrollments.services import EnrollmentService
from apps.contacts.models import Contact
from apps.courses.models import Course
from shared.constants import MAX_BULK_ENROLLMENT_SIZE
from integrations.clickfunnels.schemas import EnrollmentDTO

@pytest.fixture
def course(db):
    return Course.objects.create(cf_course_id="123", name="Test Course", workspace_id="hammer")

@pytest.fixture
def contact(db):
    return Contact.objects.create(email="student@example.com", cf_contact_id="123")

@pytest.mark.django_db
def test_enroll_contact_success(course, contact):
    mock_client = MagicMock()
    # Mock integer ID
    mock_client.enroll_contact_in_course.return_value = EnrollmentDTO(
        id=1,
        contact_id=123,
        course_id=123,
    )
    
    mock_contact_service = MagicMock()
    mock_contact_service.get_or_create_remote_contact.return_value = contact
    
    service = EnrollmentService(client=mock_client, contact_service=mock_contact_service)
    # Refactored signature: (subdomain, workspace_id, email, cf_course_id)
    result = service.enroll_contact("hammer", 198218, "student@example.com", "123")
    
    assert result.status == EnrollmentAttempt.Status.SUCCESS
    assert result.cf_enrollment_id == "1"
    assert EnrollmentAttempt.objects.count() == 1
    mock_client.enroll_contact_in_course.assert_called_once_with(
        subdomain="hammer",
        contact_id=123,
        course_id=123
    )

@pytest.mark.django_db
def test_enroll_contact_missing_course():
    mock_client = MagicMock()
    mock_contact_service = MagicMock()
    service = EnrollmentService(client=mock_client, contact_service=mock_contact_service)
    
    result = service.enroll_contact("hammer", 198218, "student@example.com", "missing_crs")
    assert result.status == EnrollmentAttempt.Status.FAILURE
    assert "not found" in result.error_message

@pytest.mark.django_db
def test_enroll_contact_failure_persistence(course, contact):
    mock_client = MagicMock()
    mock_client.enroll_contact_in_course.side_effect = Exception("CF API Error")
    
    mock_contact_service = MagicMock()
    mock_contact_service.get_or_create_remote_contact.return_value = contact
    mock_contact_service.get_by_email.return_value = contact
    
    service = EnrollmentService(client=mock_client, contact_service=mock_contact_service)
    result = service.enroll_contact("hammer", 198218, "student@example.com", "123")
    
    assert result.status == EnrollmentAttempt.Status.FAILURE
    assert "CF API Error" in result.error_log
    assert EnrollmentAttempt.objects.filter(status=EnrollmentAttempt.Status.FAILURE).count() == 1

@pytest.mark.django_db
def test_bulk_enroll_size_limit(course):
    service = EnrollmentService(client=MagicMock(), contact_service=MagicMock())
    emails = [f"s{i}@example.com" for i in range(MAX_BULK_ENROLLMENT_SIZE + 1)]
    
    with pytest.raises(ValueError, match="exceeds maximum size"):
        service.bulk_enroll("hammer", 198218, emails, "123")

@pytest.mark.django_db
def test_bulk_enroll_partial_success(course, contact):
    mock_client = MagicMock()
    # First succeeds, second fails
    mock_client.enroll_contact_in_course.side_effect = [
        EnrollmentDTO(id=1, contact_id=123, course_id=123),
        Exception("Failed")
    ]
    
    mock_contact_service = MagicMock()
    # Mock two different contacts
    c2 = Contact.objects.create(email="s2@example.com", cf_contact_id="456")
    mock_contact_service.get_or_create_remote_contact.side_effect = [contact, c2]
    mock_contact_service.get_by_email.side_effect = [contact, c2]

    service = EnrollmentService(client=mock_client, contact_service=mock_contact_service)
    result = service.bulk_enroll("hammer", 198218, ["student@example.com", "s2@example.com"], "123")
    
    assert result["total"] == 2
    assert result["success_count"] == 1
    assert result["failure_count"] == 1
    assert len(result["results"]) == 2
    assert result["results"][0].status == EnrollmentAttempt.Status.SUCCESS
    assert result["results"][1].status == EnrollmentAttempt.Status.FAILURE

@pytest.mark.django_db
def test_enrollment_payload_not_logged(course, contact, caplog):
    import logging
    logger = logging.getLogger("apps.enrollments.services")
    caplog.set_level(logging.INFO)
    
    mock_client = MagicMock()
    mock_client.enroll_contact_in_course.return_value = EnrollmentDTO(
        id=1, contact_id=123, course_id=123
    )
    
    mock_contact_service = MagicMock()
    mock_contact_service.get_or_create_remote_contact.return_value = contact
    
    service = EnrollmentService(client=mock_client, contact_service=mock_contact_service)
    service.enroll_contact("hammer", 198218, "student@example.com", "123")
    
    log_text = caplog.text
    assert "student@example.com" not in log_text
    assert "198218" in log_text
