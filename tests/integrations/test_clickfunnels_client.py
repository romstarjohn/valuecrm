import json
import pytest
import requests
import responses
import logging
from integrations.clickfunnels.client import ClickFunnelsClient
from integrations.clickfunnels.exceptions import ClickFunnelsAPIError, ClickFunnelsAuthError, ClickFunnelsRateLimitError
from integrations.clickfunnels.schemas import ContactDTO, WorkspaceDTO, EnrollmentDTO, TeamDTO

@pytest.fixture
def client():
    return ClickFunnelsClient(api_access_token="test_token", api_user_agent="TestUA/1.0")

@responses.activate
def test_validate_credentials_uses_teams_endpoint(client):
    responses.add(
        responses.GET,
        "https://accounts.myclickfunnels.com/api/v2/teams",
        json=[{"id": 123, "public_id": "abc", "name": "Team"}],
        status=200
    )
    result = client.validate_credentials()
    assert result[0]["id"] == 123
    
    # Verify headers
    request = responses.calls[0].request
    assert request.headers["Authorization"] == "Bearer test_token"
    assert request.headers["User-Agent"] == "TestUA/1.0"

@responses.activate
def test_list_teams_verified_contract(client):
    # Verified sample from docs
    sample_response = [
        {
            "id": 193460,
            "public_id": "JNzNaa",
            "name": "Richard Steinmetz's Team",
            "extra_field": "ignore me"
        }
    ]
    responses.add(
        responses.GET,
        "https://accounts.myclickfunnels.com/api/v2/teams",
        json=sample_response,
        status=200
    )
    teams = client.list_teams()
    assert len(teams) == 1
    assert isinstance(teams[0], TeamDTO)
    assert teams[0].id == 193460
    assert teams[0].public_id == "JNzNaa"

@responses.activate
def test_list_workspaces_verified_contract(client):
    # Verified sample from docs
    sample_response = [
        {
            "id": 198218,
            "public_id": "example_public_id",
            "team_id": 193460,
            "name": "Hammer Workspace",
            "subdomain": "hammer-time"
        }
    ]
    responses.add(
        responses.GET,
        "https://accounts.myclickfunnels.com/api/v2/teams/193460/workspaces",
        json=sample_response,
        status=200
    )
    workspaces = client.list_workspaces(193460)
    assert len(workspaces) == 1
    assert isinstance(workspaces[0], WorkspaceDTO)
    assert workspaces[0].id == 198218
    assert workspaces[0].team_id == 193460
    assert workspaces[0].subdomain == "hammer-time"

@responses.activate
def test_get_contact_by_email_verified_contract(client):
    # Verified sample from docs — real response field is "email_address"
    sample_response = [
        {
            "id": 33,
            "email_address": "example@example.com"
        }
    ]
    responses.add(
        responses.GET,
        "https://hammer.myclickfunnels.com/api/v2/workspaces/198218/contacts",
        json=sample_response,
        status=200
    )
    contact = client.get_contact_by_email("hammer", 198218, "example@example.com")
    assert contact.id == 33
    assert contact.email_address == "example@example.com"

    # Must filter via "filter[email_address]", not a bare "email" param —
    # ClickFunnels silently ignores unrecognized query params.
    request_url = responses.calls[0].request.url
    assert "filter%5Bemail_address%5D=example%40example.com" in request_url

@responses.activate
def test_create_contact_wraps_payload_and_translates_email(client):
    responses.add(
        responses.POST,
        "https://hammer.myclickfunnels.com/api/v2/workspaces/198218/contacts",
        json={"id": 44, "email_address": "new@example.com"},
        status=201,
    )
    contact = client.create_contact("hammer", 198218, {"email": "new@example.com", "first_name": "New"})
    assert contact.id == 44
    assert contact.email_address == "new@example.com"

    sent_body = responses.calls[0].request.body
    sent = json.loads(sent_body)
    assert sent == {"contact": {"email_address": "new@example.com", "first_name": "New"}}


@responses.activate
def test_enroll_contact_in_course_uses_courses_path(client):
    responses.add(
        responses.POST,
        "https://hammer.myclickfunnels.com/api/v2/courses/123/enrollments",
        json={"id": 1, "contact_id": 44, "course_id": 123},
        status=201,
    )
    enrollment = client.enroll_contact_in_course("hammer", contact_id=44, course_id=123)
    assert enrollment.id == 1

    sent_body = responses.calls[0].request.body
    sent = json.loads(sent_body)
    assert sent == {"courses_enrollment": {"contact_id": 44}}


@responses.activate
def test_logging_security(client, caplog):
    responses.add(
        responses.GET,
        "https://accounts.myclickfunnels.com/api/v2/teams",
        json=[{"id": 123, "public_id": "abc", "name": "Team"}],
        status=200
    )
    
    caplog.set_level(logging.INFO)
    client.validate_credentials()
    
    # Check individual records for the string
    for record in caplog.records:
        # Verify token is NOT leaked in any record
        assert "test_token" not in record.getMessage()
        assert "Authorization" not in record.getMessage()
