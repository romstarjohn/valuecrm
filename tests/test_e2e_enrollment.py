import pytest
import responses
from django.test import Client
from apps.configuration.models import ClickFunnelsConfig
from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.enrollments.models import EnrollmentAttempt
from shared.security import encrypt_value

@pytest.fixture
def e2e_setup(db):
    config = ClickFunnelsConfig.objects.create(
        name="Active CF",
        is_active=True,
        workspace_id="198218",
        workspace_subdomain="hammer",
        api_access_token=encrypt_value("token_e2e")
    )
    course = Course.objects.create(
        cf_course_id="123",
        workspace_id="hammer",
        name="E2E Course"
    )
    return config, course

@responses.activate
@pytest.mark.django_db
def test_e2e_successful_enrollment(e2e_setup, staff_client):
    config, course = e2e_setup
    
    email = "new_student@example.com"
    
    # 1. Mock Contact Search (Not found)
    # Correct URL pattern: /workspaces/{id}/contacts
    responses.add(
        responses.GET,
        f"https://hammer.myclickfunnels.com/api/v2/workspaces/198218/contacts?email={email}",
        json=[],
        status=200
    )
    
    # 2. Mock Contact Creation
    responses.add(
        responses.POST,
        "https://hammer.myclickfunnels.com/api/v2/workspaces/198218/contacts",
        json={"id": 33, "email": email},
        status=201
    )
    
    # 3. Mock Enrollment
    responses.add(
        responses.POST,
        "https://hammer.myclickfunnels.com/api/v2/workspaces/198218/course_enrollments",
        json={
            "id": 1, 
            "contact_id": 33, 
            "course_id": 123,
            "status": "active"
        },
        status=201
    )
    
    payload = {"email": email, "course_id": "123"}
    response = staff_client.post("/api/enrollments/", data=payload, content_type="application/json")
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "SUCCESS"
    assert data["contact_email"] == email
    
    # Verify DB records
    assert Contact.objects.filter(email=email, cf_contact_id="33").exists()
    assert EnrollmentAttempt.objects.filter(cf_enrollment_id="1").exists()

@responses.activate
@pytest.mark.django_db
def test_e2e_bulk_partial_failure(e2e_setup, staff_client):
    config, course = e2e_setup
    
    email1 = "s1@example.com"
    email2 = "s2@example.com"
    
    # Mocking for student 1 (Success)
    responses.add(
        responses.GET,
        f"https://hammer.myclickfunnels.com/api/v2/workspaces/198218/contacts?email={email1}",
        json=[{"id": 101, "email": email1}],
        status=200
    )
    responses.add(
        responses.POST,
        "https://hammer.myclickfunnels.com/api/v2/workspaces/198218/course_enrollments",
        json={
            "id": 201,
            "contact_id": 101,
            "course_id": 123,
            "status": "active"
        },
        status=201
    )
    
    # Mocking for student 2 (Failure)
    responses.add(
        responses.GET,
        f"https://hammer.myclickfunnels.com/api/v2/workspaces/198218/contacts?email={email2}",
        json=[{"id": 102, "email": email2}],
        status=200
    )
    responses.add(
        responses.POST,
        "https://hammer.myclickfunnels.com/api/v2/workspaces/198218/course_enrollments",
        json={"message": "Already enrolled"},
        status=422
    )
    
    payload = {
        "emails": [email1, email2],
        "course_id": "123"
    }
    response = staff_client.post("/api/enrollments/bulk", data=payload, content_type="application/json")
    
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert data["success_count"] == 1
    assert data["failure_count"] == 1
    
    # Verify two audit records exist
    assert EnrollmentAttempt.objects.filter(contact__email=email1, status="SUCCESS").exists()
    assert EnrollmentAttempt.objects.filter(contact__email=email2, status="FAILURE").exists()
