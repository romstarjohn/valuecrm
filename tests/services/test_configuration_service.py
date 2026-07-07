import pytest
from unittest.mock import MagicMock
from apps.configuration.models import ClickFunnelsConfig
from apps.configuration.services import ConfigurationService
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
def test_verify_token_success(mocker, active_config):
    mock_client = MagicMock()
    mock_client.validate_credentials.return_value = {"status": "ok"}
    
    service = ConfigurationService(client=mock_client)
    result = service.verify_token(active_config)
    
    assert result is True
    assert active_config.validation_status == ClickFunnelsConfig.ValidationStatus.VALID
    mock_client.validate_credentials.assert_called_once()

@pytest.mark.django_db
def test_verify_token_failure(mocker, active_config):
    mock_client = MagicMock()
    mock_client.validate_credentials.side_effect = Exception("Auth Failed")
    
    service = ConfigurationService(client=mock_client)
    
    with pytest.raises(Exception):
        service.verify_token(active_config)
    
    active_config.refresh_from_db()
    assert active_config.validation_status == ClickFunnelsConfig.ValidationStatus.INVALID

@pytest.mark.django_db
def test_one_active_config_only():
    c1 = ClickFunnelsConfig.objects.create(name="C1", is_active=True, api_access_token="t")
    c2 = ClickFunnelsConfig.objects.create(name="C2", is_active=True, api_access_token="t")
    
    c1.refresh_from_db()
    assert c1.is_active is False
    assert c2.is_active is True
    
    c1.is_active = True
    c1.save()
    
    c2.refresh_from_db()
    assert c2.is_active is False
