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
def test_validate_credentials_uses_accounts_base(client):
    responses.add(
        responses.GET,
        "https://accounts.myclickfunnels.com/api/v2/accounts",
        json={"id": 123},
        status=200
    )
    result = client.validate_credentials()
    assert result["id"] == 123
    
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
    # Verified sample from docs
    sample_response = [
        {
            "id": 33,
            "email": "example@example.com"
        }
    ]
    responses.add(
        responses.GET,
        "https://hammer.myclickfunnels.com/api/v2/workspaces/198218/contacts?email=example@example.com",
        json=sample_response,
        status=200
    )
    contact = client.get_contact_by_email("hammer", 198218, "example@example.com")
    assert contact.id == 33
    assert contact.email == "example@example.com"

@responses.activate
def test_logging_security(client, caplog):
    responses.add(
        responses.GET,
        "https://accounts.myclickfunnels.com/api/v2/accounts",
        json={"status": "ok"},
        status=200
    )
    
    caplog.set_level(logging.INFO)
    client.validate_credentials()
    
    # Check individual records for the string
    for record in caplog.records:
        # Verify token is NOT leaked in any record
        assert "test_token" not in record.getMessage()
        assert "Authorization" not in record.getMessage()
