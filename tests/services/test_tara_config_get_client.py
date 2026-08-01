import pytest

from apps.payments.models import TaraConfig
from apps.payments.services import TaraConfigService
from integrations.payments.tara.client import TaraClient
from integrations.payments.tara.exceptions import TaraConfigurationError, TaraCredentialError
from shared.security import encrypt_value

pytestmark = pytest.mark.django_db


def test_no_active_config_raises_configuration_error():
    with pytest.raises(TaraConfigurationError):
        TaraConfigService().get_client()


def test_disabled_configuration_raises_configuration_error():
    TaraConfig.objects.create(
        name="Main", business_id="biz_1", is_active=False,
        api_key=encrypt_value("key"), webhook_secret=encrypt_value("secret"),
    )
    with pytest.raises(TaraConfigurationError):
        TaraConfigService().get_client()


def test_missing_business_id_raises_configuration_error():
    TaraConfig.objects.create(
        name="Main", business_id="", is_active=True,
        api_key=encrypt_value("key"), webhook_secret=encrypt_value("secret"),
    )
    with pytest.raises(TaraConfigurationError):
        TaraConfigService().get_client()


def test_secret_decryption_failure_raises_credential_error(settings):
    TaraConfig.objects.create(
        name="Main", business_id="biz_1", is_active=True,
        api_key=encrypt_value("key"), webhook_secret=encrypt_value("secret"),
    )
    # Simulate a rotated/lost encryption key: ciphertext no longer decrypts under the current key.
    from cryptography.fernet import Fernet
    settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode()

    with pytest.raises(TaraCredentialError):
        TaraConfigService().get_client()


def test_successful_client_construction_returns_usable_client():
    TaraConfig.objects.create(
        name="Main", business_id="biz_1", is_active=True,
        api_key=encrypt_value("real-key"), webhook_secret=encrypt_value("real-secret"),
    )
    client = TaraConfigService().get_client()
    assert isinstance(client, TaraClient)
    assert client.business_id == "biz_1"
    assert client.api_key == "real-key"


def test_no_credentials_in_configuration_errors():
    TaraConfig.objects.create(name="Main", business_id="", is_active=True)
    try:
        TaraConfigService().get_client()
    except TaraConfigurationError as e:
        assert "biz" not in str(e)  # no leaked business_id fragments either, though it's not secret
    else:
        pytest.fail("expected TaraConfigurationError")


def test_no_credentials_in_credential_errors(settings):
    from cryptography.fernet import Fernet

    TaraConfig.objects.create(
        name="Main", business_id="biz_1", is_active=True,
        api_key=encrypt_value("super-secret-key-value"), webhook_secret=encrypt_value("super-secret-whsec"),
    )
    settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode()

    try:
        TaraConfigService().get_client()
    except TaraCredentialError as e:
        assert "super-secret-key-value" not in str(e)
        assert "super-secret-whsec" not in str(e)
    else:
        pytest.fail("expected TaraCredentialError")
