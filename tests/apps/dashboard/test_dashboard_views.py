import pytest
from django.urls import reverse
from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.enrollments.models import EnrollmentAttempt
from apps.configuration.models import ClickFunnelsConfig

@pytest.mark.django_db
def test_dashboard_requires_login(client):
    url = reverse("dashboard:index")
    response = client.get(url)
    assert response.status_code == 302
    assert "/admin/login/" in response.url

@pytest.mark.django_db
def test_dashboard_renders_metrics(staff_client):
    # Setup data
    Contact.objects.create(email="c1@example.com")
    Course.objects.create(cf_course_id="crs_1", name="Course 1", workspace_id="ws_1")
    
    url = reverse("dashboard:index")
    response = staff_client.get(url)
    
    assert response.status_code == 200
    content = response.content.decode()
    assert "Dashboard" in content
    # Metrics
    assert "Contacts" in content
    assert "Courses" in content
    assert "Total Attempts" in content

@pytest.mark.django_db
def test_dashboard_renders_integration_status(staff_client):
    ClickFunnelsConfig.objects.create(
        name="Main Team",
        is_active=True,
        workspace_name="Target Workspace",
        workspace_id="ws_123",
        validation_status="VALID"
    )
    
    url = reverse("dashboard:index")
    response = staff_client.get(url)
    
    assert response.status_code == 200
    content = response.content.decode()
    assert "Target Workspace" in content
    assert "VALID" in content

@pytest.mark.django_db
def test_dashboard_handles_empty_state(staff_client):
    url = reverse("dashboard:index")
    response = staff_client.get(url)
    
    assert response.status_code == 200
    content = response.content.decode()
    assert "No recent activity" in content
    assert "Newest Contacts" in content

@pytest.mark.django_db
def test_dashboard_renders_failures_alert(staff_client):
    contact = Contact.objects.create(email="fail@example.com")
    course = Course.objects.create(cf_course_id="c1", name="C1", workspace_id="w1")
    EnrollmentAttempt.objects.create(
        contact=contact,
        course=course,
        status=EnrollmentAttempt.Status.FAILURE
    )
    
    url = reverse("dashboard:index")
    response = staff_client.get(url)
    
    assert response.status_code == 200
    content = response.content.decode()
    assert "Attention Required: Recent Failures" in content
