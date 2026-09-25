import pytest
from django.urls import reverse
from unittest.mock import MagicMock
from apps.configuration.models import ClickFunnelsConfig
from shared.security import encrypt_value
from integrations.clickfunnels.schemas import WorkspaceDTO, TeamDTO

@pytest.fixture
def active_config(db):
    return ClickFunnelsConfig.objects.create(
        name="Test Config",
        is_active=True,
        api_access_token=encrypt_value("old-token"),
        api_user_agent="TestUA/1.0"
    )

@pytest.mark.django_db
def test_settings_page_requires_login(client):
    url = reverse("configuration:settings")
    response = client.get(url)
    assert response.status_code == 302
    assert "/connexion/" in response.url

@pytest.mark.django_db
def test_verify_token_flow(mocker, staff_client, active_config):
    mock_client = MagicMock()
    mock_client.validate_credentials.return_value = {"status": "ok"}
    mock_client.list_teams.return_value = [TeamDTO(id=193460, public_id="JNzNaa", name="Team 1")]
    
    mocker.patch("apps.configuration.views.ClickFunnelsClient.from_configuration", return_value=mock_client)
    
    url = reverse("configuration:settings")
    response = staff_client.post(url, data={"action": "verify_token"})
    assert response.status_code == 302
    
    active_config.refresh_from_db()
    assert active_config.validation_status == ClickFunnelsConfig.ValidationStatus.VALID
    assert active_config.raw_payload["available_teams"][0]["id"] == 193460

@pytest.mark.django_db
def test_select_team_flow(mocker, staff_client, active_config):
    active_config.raw_payload["available_teams"] = [{"id": 193460, "public_id": "p1", "name": "Team 1"}]
    active_config.save()
    
    mock_client = MagicMock()
    mock_client.list_workspaces.return_value = [
        WorkspaceDTO(id=198218, public_id="p2", team_id=193460, name="WS 1", subdomain="test-sub")
    ]
    mocker.patch("apps.configuration.views.ClickFunnelsClient.from_configuration", return_value=mock_client)
    
    url = reverse("configuration:settings")
    response = staff_client.post(url, data={"action": "select_team", "team_id": 193460})
    assert response.status_code == 302
    
    active_config.refresh_from_db()
    assert active_config.team_id == "193460"
    assert active_config.raw_payload["available_workspaces"][0]["id"] == 198218

@pytest.mark.django_db
def test_select_workspace_flow(staff_client, active_config):
    active_config.raw_payload["available_workspaces"] = [
        {"id": 198218, "public_id": "p2", "team_id": 193460, "name": "WS 1", "subdomain": "test-sub"}
    ]
    active_config.save()
    
    url = reverse("configuration:settings")
    response = staff_client.post(url, data={"action": "select_workspace", "workspace_id": 198218})
    assert response.status_code == 302
    
    active_config.refresh_from_db()
    assert active_config.workspace_id == "198218"
    assert active_config.workspace_subdomain == "test-sub"

@pytest.mark.django_db
def test_workspace_subdomain_is_mandatory(staff_client, active_config):
    # Workspace without subdomain
    active_config.raw_payload["available_workspaces"] = [
        {"id": 198218, "public_id": "p2", "team_id": 193460, "name": "WS 1"}
    ]
    active_config.save()
    
    url = reverse("configuration:settings")
    response = staff_client.post(url, data={"action": "select_workspace", "workspace_id": 198218}, follow=True)
    
    assert "Workspace subdomain was not returned by ClickFunnels" in response.content.decode()
    active_config.refresh_from_db()
    assert active_config.workspace_subdomain is None



@pytest.mark.django_db
def test_clickfunnels_page_plain_status_and_separate_test_button(staff_client, active_config):
    import html
    content = html.unescape(staff_client.get(reverse("configuration:settings")).content.decode())
    assert "Connexion ClickFunnels" in content
    assert "Que faire ?" in content or "Connecté ✓" in content
    assert "Tester la connexion" in content
    # Testing must live in its own form: clicking it must never look like it saves a newly typed key.
    save_form_end = content.index('value="save_token"')
    save_form_close = content.index("</form>", save_form_end)
    assert content.index('value="verify_token"') > save_form_close
