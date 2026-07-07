import pytest
from django.urls import reverse
from unittest.mock import MagicMock
from apps.enrollments.models import EnrollmentAttempt
from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.configuration.models import ClickFunnelsConfig
from apps.enrollments.schemas import EnrollmentResultDTO
from shared.security import encrypt_value

@pytest.fixture
def setup_data(db):
    config = ClickFunnelsConfig.objects.create(
        name="Active CF",
        is_active=True,
        workspace_id="198218",
        workspace_subdomain="hammer",
        api_access_token=encrypt_value("token")
    )
    course = Course.objects.create(
        cf_course_id="crs_123",
        name="Test Course",
        workspace_id="hammer"
    )
    contact = Contact.objects.create(email="test@example.com", first_name="Test", last_name="User")
    return config, course, contact

@pytest.mark.django_db
def test_enrollment_list_view(staff_client, setup_data):
    config, course, contact = setup_data
    EnrollmentAttempt.objects.create(
        contact=contact,
        course=course,
        status=EnrollmentAttempt.Status.SUCCESS
    )
    url = reverse("enrollments:list")
    response = staff_client.get(url)
    assert response.status_code == 200
    assert contact.email in response.content.decode()

@pytest.mark.django_db
def test_enrollment_new_view_post_success(mocker, staff_client, setup_data):
    config, course, contact = setup_data
    
    mock_result = EnrollmentResultDTO(
        contact_email=contact.email,
        course_id=course.cf_course_id,
        status=EnrollmentAttempt.Status.SUCCESS,
        enrollment_attempt_id=1
    )
    
    # Mock service directly to avoid full orchestration in view test
    mocker.patch("apps.enrollments.views.EnrollmentService.enroll_contact", return_value=mock_result)
    mocker.patch("apps.enrollments.views.ClickFunnelsClient.from_configuration", return_value=MagicMock())
    
    url = reverse("enrollments:new")
    payload = {
        "email": contact.email,
        "course_id": course.cf_course_id
    }
    response = staff_client.post(url, data=payload)
    
    # Successful enrollment redirects to detail
    assert response.status_code == 302
    assert "/enrollments/1/" in response.url

@pytest.mark.django_db
def test_enrollment_bulk_view_post(mocker, staff_client, setup_data):
    config, course, contact = setup_data
    
    mock_bulk_result = {
        "total": 1,
        "success_count": 1,
        "failure_count": 0,
        "results": [
            EnrollmentResultDTO(
                contact_email=contact.email,
                course_id=course.cf_course_id,
                status=EnrollmentAttempt.Status.SUCCESS,
                enrollment_attempt_id=1
            )
        ]
    }
    
    mocker.patch("apps.enrollments.views.EnrollmentService.bulk_enroll", return_value=mock_bulk_result)
    mocker.patch("apps.enrollments.views.ClickFunnelsClient.from_configuration", return_value=MagicMock())
    
    url = reverse("enrollments:bulk")
    payload = {
        "emails": contact.email,
        "course_id": course.cf_course_id
    }
    response = staff_client.post(url, data=payload)
    
    assert response.status_code == 200
    assert "Bulk Processing Summary" in response.content.decode()

@pytest.mark.django_db
def test_enrollment_detail_view(staff_client, setup_data):
    config, course, contact = setup_data
    attempt = EnrollmentAttempt.objects.create(
        contact=contact,
        course=course,
        status=EnrollmentAttempt.Status.SUCCESS,
        cf_enrollment_id="cf_enr_123"
    )
    url = reverse("enrollments:detail", kwargs={"pk": attempt.pk})
    response = staff_client.get(url)
    assert response.status_code == 200
    assert "cf_enr_123" in response.content.decode()
