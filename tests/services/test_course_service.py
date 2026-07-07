import pytest
from unittest.mock import MagicMock
from apps.courses.models import Course
from apps.courses.services import CourseService
from integrations.clickfunnels.schemas import CourseDTO

@pytest.mark.django_db
def test_sync_courses_creates_and_updates():
    mock_client = MagicMock()
    # Verified sample ID: 198218
    # Using official Course object structure with 'title'
    mock_client.list_courses.return_value = [
        CourseDTO(id=1, title="Course 1", description="Desc 1", current_path="/c1"),
        CourseDTO(id=2, title="Course 2", description="Desc 2", current_path="/c2"),
    ]
    
    # Pre-existing course to test update
    Course.objects.create(cf_course_id="1", name="Old Name", workspace_id="ws_123")
    
    service = CourseService(client=mock_client)
    result = service.sync_courses(workspace_id=198218, workspace_subdomain="hammer")
    
    assert result["synced_count"] == 2
    assert result["created_count"] == 1
    assert result["updated_count"] == 1
    
    course_1 = Course.objects.get(cf_course_id="1")
    assert course_1.name == "Course 1"
    assert course_1.workspace_id == "hammer"
    
    # Verify client call uses correct args
    mock_client.list_courses.assert_called_once_with("hammer", 198218)

@pytest.mark.django_db
def test_sync_courses_regression_official_sample():
    """
    Regression test using exact sample provided in contract.
    """
    sample = {
      "id": 331,
      "public_id": None,
      "title": "My Course",
      "published_at": None,
      "root_section_id": None,
      "description": "Course description",
      "current_path": "/courses/my-course",
      "sharing_fingerprint": None,
      "show_in_community": True,
      "show_to_non_members": False,
      "upgrade_url": None,
      "redirect_to_full_course": None,
      "unauthorized_redirect_url": None,
      "comments_enabled": True,
      "created_at": None,
      "updated_at": None,
      "image_url": "https://example.com/course.png",
      "new_api_field": "should be allowed"
    }
    
    dto = CourseDTO.model_validate(sample)
    assert dto.id == 331
    assert dto.title == "My Course"
    assert dto.description == "Course description"
    
    mock_client = MagicMock()
    mock_client.list_courses.return_value = [dto]
    
    service = CourseService(client=mock_client)
    service.sync_courses(workspace_id=198218, workspace_subdomain="hammer")
    
    course = Course.objects.get(cf_course_id="331")
    assert course.name == "My Course"
    assert course.description == "Course description"
    assert course.raw_payload["id"] == 331
    assert course.raw_payload["new_api_field"] == "should be allowed"

@pytest.mark.django_db
def test_sync_courses_root_section_id_int():
    """
    Regression test for integer root_section_id.
    """
    sample = {
      "id": 331,
      "title": "My Course",
      "description": "Course description",
      "root_section_id": 174729
    }
    
    dto = CourseDTO.model_validate(sample)
    assert dto.root_section_id == 174729
    
    mock_client = MagicMock()
    mock_client.list_courses.return_value = [dto]
    
    service = CourseService(client=mock_client)
    service.sync_courses(workspace_id=198218, workspace_subdomain="hammer")
    
    course = Course.objects.get(cf_course_id="331")
    assert course.name == "My Course"

@pytest.mark.django_db
def test_course_payload_not_logged(caplog):
    import logging
    logger = logging.getLogger("apps.courses.services")
    caplog.set_level(logging.INFO)
    
    mock_client = MagicMock()
    mock_client.list_courses.return_value = [
        CourseDTO(id=1, title="Course 1", description="Desc 1")
    ]
    
    service = CourseService(client=mock_client)
    service.sync_courses(198218, "hammer")
    
    log_text = caplog.text
    # 'title' should not be in logs if we are scrubbing
    assert "Course 1" not in log_text
    # workspace_id is whitelisted in sanitize_data
    assert "198218" in log_text
