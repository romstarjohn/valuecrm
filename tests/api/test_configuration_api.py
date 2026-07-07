import pytest
from unittest.mock import MagicMock
from django.test import Client
from apps.configuration.models import ClickFunnelsConfig
from shared.security import encrypt_value

@pytest.mark.django_db
def test_get_active_configuration_api(staff_client):
    # No active config initially
    response = staff_client.get("/api/config/active")
    assert response.status_code == 404
    
    # Create active config
    ClickFunnelsConfig.objects.create(
        name="Active One",
        is_active=True,
        api_access_token=encrypt_value("token"),
        workspace_name="My WS"
    )
    
    response = staff_client.get("/api/config/active")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Active One"
    assert data["workspace_name"] == "My WS"

@pytest.mark.django_db
def test_verify_configuration_api(mocker, staff_client):
    # Create with properly encrypted keys for the service to decrypt
    config = ClickFunnelsConfig.objects.create(
        name="To Verify",
        api_access_token=encrypt_value("token-api")
    )
    
    # Mock the service methods since the API view now calls multi-step flow
    mock_service = MagicMock()
    # verify_token, fetch_teams, fetch_workspaces
    mock_service.verify_token.return_value = True
    mock_service.fetch_teams.return_value = []
    
    mocker.patch("apps.configuration.api.ConfigurationService", return_value=mock_service)
    mocker.patch("apps.configuration.api.ClickFunnelsClient.from_configuration", return_value=MagicMock())
    
    response = staff_client.post(f"/api/config/{config.id}/verify")
    assert response.status_code == 200
    
    mock_service.verify_token.assert_called_once_with(config)
    mock_service.fetch_teams.assert_called_once_with(config)
