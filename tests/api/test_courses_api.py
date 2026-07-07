import pytest
from unittest.mock import MagicMock
from django.test import Client
from apps.courses.models import Course
from apps.configuration.models import ClickFunnelsConfig
from integrations.clickfunnels.schemas import CourseDTO
from shared.security import encrypt_value

@pytest.mark.django_db
def test_list_courses_api(staff_client):
    Course.objects.create(cf_course_id="c1", name="C1", workspace_id="ws1")
    
    response = staff_client.get("/api/courses/")
    assert response.status_code == 200
    assert len(response.json()) == 1

@pytest.mark.django_db
def test_sync_courses_api_success(mocker, staff_client):
    # Setup active config
    ClickFunnelsConfig.objects.create(
        name="Active", 
        is_active=True, 
        workspace_id="198218",
        workspace_subdomain="hammer",
        api_access_token=encrypt_value("token")
    )
    
    mock_cf_client = MagicMock()
    mock_cf_client.list_courses.return_value = [
        CourseDTO(id=1, title="API Course", description="API Desc")
    ]
    mocker.patch("apps.courses.api.ClickFunnelsClient.from_configuration", return_value=mock_cf_client)
    
    # Endpoint doesn't need payload now, uses active config
    response = staff_client.post("/api/courses/sync")
    
    assert response.status_code == 200
    assert response.json()["synced_count"] == 1
    assert Course.objects.filter(cf_course_id="1").exists()
    mock_cf_client.list_courses.assert_called_once_with("hammer", 198218)

@pytest.mark.django_db
def test_sync_courses_api_no_config(staff_client):
    response = staff_client.post("/api/courses/sync")
    assert response.status_code == 404
    assert response.json()["message"] == "No active configuration found"
