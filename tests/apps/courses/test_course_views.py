import pytest
from django.urls import reverse
from unittest.mock import MagicMock
from apps.courses.models import Course
from apps.configuration.models import ClickFunnelsConfig
from integrations.clickfunnels.schemas import CourseDTO
from shared.security import encrypt_value

@pytest.mark.django_db
def test_course_list_view(staff_client):
    Course.objects.create(cf_course_id="crs_1", name="Test Course", workspace_id="ws_1")
    url = reverse("courses:list")
    response = staff_client.get(url)
    assert response.status_code == 200
    assert "Test Course" in response.content.decode()

@pytest.mark.django_db
def test_course_detail_view(staff_client):
    course = Course.objects.create(cf_course_id="crs_1", name="Detail Course", workspace_id="ws_1")
    url = reverse("courses:detail", kwargs={"cf_course_id": course.cf_course_id})
    response = staff_client.get(url)
    assert response.status_code == 200
    assert "Detail Course" in response.content.decode()

@pytest.mark.django_db
def test_course_sync_view_get(staff_client):
    url = reverse("courses:sync")
    response = staff_client.get(url)
    assert response.status_code == 200
    assert "Synchroniser les formations" in response.content.decode()

@pytest.mark.django_db
def test_course_sync_view_post_success(mocker, staff_client):
    # Setup active config with workspace_id and subdomain
    config = ClickFunnelsConfig.objects.create(
        name="Active CF",
        is_active=True,
        workspace_id="198218",
        workspace_subdomain="hammer",
        api_access_token=encrypt_value("token")
    )
    
    mock_client = MagicMock()
    mock_client.list_courses.return_value = [
        CourseDTO(id=1, title="Synced Course", description="Synced Desc")
    ]
    mocker.patch("apps.courses.views.ClickFunnelsClient.from_configuration", return_value=mock_client)
    
    url = reverse("courses:sync")
    response = staff_client.post(url)
    
    assert response.status_code == 302
    assert Course.objects.filter(cf_course_id="1").exists()
    mock_client.list_courses.assert_called_once_with("hammer", 198218)

@pytest.mark.django_db
def test_course_sync_view_missing_config_fails(staff_client):
    # No config
    url = reverse("courses:sync")
    response = staff_client.post(url, follow=True)
    assert "Aucune configuration ClickFunnels active trouvée" in response.content.decode()

@pytest.mark.django_db
def test_course_sync_view_missing_workspace_fails(staff_client):
    # Config exists but no workspace
    ClickFunnelsConfig.objects.create(
        name="No WS",
        is_active=True,
        api_access_token=encrypt_value("token")
    )
    url = reverse("courses:sync")
    response = staff_client.post(url, follow=True)
    assert "Sélectionnez un espace de travail avant de synchroniser les formations" in response.content.decode()
