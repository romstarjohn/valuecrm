import pytest
from unittest.mock import MagicMock
from django.test import Client
from apps.configuration.models import ClickFunnelsConfig
from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.enrollments.models import EnrollmentAttempt
from apps.enrollments.schemas import EnrollmentResultDTO
from shared.security import encrypt_value

@pytest.fixture
def setup_data(db):
    config = ClickFunnelsConfig.objects.create(
        name="Active", 
        is_active=True, 
        workspace_id="198218",
        workspace_subdomain="hammer",
        api_access_token=encrypt_value("token")
    )
    course = Course.objects.create(cf_course_id="crs_1", name="C1", workspace_id="hammer")
    contact = Contact.objects.create(email="test@example.com", cf_contact_id="con_1")
    return config, course, contact

@pytest.mark.django_db
def test_single_enrollment_api(mocker, setup_data, staff_client):
    config, course, contact = setup_data
    
    mock_result = EnrollmentResultDTO(
        contact_email="test@example.com",
        course_id="crs_1",
        status=EnrollmentAttempt.Status.SUCCESS,
        enrollment_attempt_id=123
    )
    mocker.patch("apps.enrollments.services.EnrollmentService.enroll_contact", return_value=mock_result)
    
    payload = {"email": "test@example.com", "course_id": "crs_1"}
    response = staff_client.post("/api/enrollments/", data=payload, content_type="application/json")
    
    assert response.status_code == 200
    assert response.json()["status"] == "SUCCESS"
    assert response.json()["enrollment_attempt_id"] == 123

@pytest.mark.django_db
def test_bulk_enrollment_api(mocker, setup_data, staff_client):
    config, course, contact = setup_data
    
    mock_result = EnrollmentResultDTO(
        contact_email="test@example.com",
        course_id="crs_1",
        status=EnrollmentAttempt.Status.SUCCESS,
        enrollment_attempt_id=123
    )
    mocker.patch("apps.enrollments.services.EnrollmentService.bulk_enroll", return_value={
        "total": 1,
        "success_count": 1,
        "failure_count": 0,
        "results": [mock_result]
    })
    
    payload = {"emails": ["test@example.com"], "course_id": "crs_1"}
    response = staff_client.post("/api/enrollments/bulk", data=payload, content_type="application/json")
    
    assert response.status_code == 200
    assert response.json()["success_count"] == 1
    assert len(response.json()["results"]) == 1
