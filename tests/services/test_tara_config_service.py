import pytest
from cryptography.fernet import Fernet, InvalidToken
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.payments.migrations._encrypt_legacy_credentials import encrypt_legacy_plaintext_credentials
from apps.payments.models import TaraConfig
from apps.payments.services import TaraConfigService
from shared.security import decrypt_value, encrypt_value


@pytest.fixture
def service():
    return TaraConfigService()


@pytest.mark.django_db
def test_update_credentials_encrypts_and_stores(service):
    config = TaraConfig(name="Main", business_id="biz_1")
    service.update_credentials(config, "raw-api-key", "raw-webhook-secret")

    # Stored value must not be the plaintext itself...
    assert config.api_key != "raw-api-key"
    assert config.webhook_secret != "raw-webhook-secret"
    # ...but must decrypt back to it server-side.
    assert decrypt_value(config.api_key) == "raw-api-key"
    assert decrypt_value(config.webhook_secret) == "raw-webhook-secret"


@pytest.mark.django_db
def test_update_credentials_respects_commit_false_contract(service):
    """update_credentials only mutates the in-memory instance; persistence is the caller's job."""
    config = TaraConfig(name="Main", business_id="biz_1")
    service.update_credentials(config, "raw-api-key", "raw-webhook-secret")
    assert config.pk is None
    assert not TaraConfig.objects.exists()


@pytest.mark.django_db
def test_blank_values_leave_existing_credentials_unchanged(service):
    config = TaraConfig.objects.create(
        name="Main", business_id="biz_1",
        api_key=encrypt_value("original-key"), webhook_secret=encrypt_value("original-secret"),
    )
    service.update_credentials(config, "", "")
    assert decrypt_value(config.api_key) == "original-key"
    assert decrypt_value(config.webhook_secret) == "original-secret"

    service.update_credentials(config, None, None)
    assert decrypt_value(config.api_key) == "original-key"
    assert decrypt_value(config.webhook_secret) == "original-secret"


@pytest.mark.django_db
def test_explicit_replacement_updates_only_the_given_secret(service):
    config = TaraConfig.objects.create(
        name="Main", business_id="biz_1",
        api_key=encrypt_value("original-key"), webhook_secret=encrypt_value("original-secret"),
    )
    service.update_credentials(config, "new-key", "")
    assert decrypt_value(config.api_key) == "new-key"
    assert decrypt_value(config.webhook_secret) == "original-secret"  # untouched


@pytest.mark.django_db
def test_get_active_config_returns_only_active(service):
    TaraConfig.objects.create(name="Inactive", business_id="b1", is_active=False)
    active = TaraConfig.objects.create(name="Active", business_id="b2", is_active=True)
    assert service.get_active_config() == active


def test_missing_encryption_key_raises(settings):
    settings.FIELD_ENCRYPTION_KEY = None
    with pytest.raises(ValueError):
        encrypt_value("some-secret")
    with pytest.raises(ValueError):
        decrypt_value("some-token")


def test_incorrect_encryption_key_raises_invalid_token(settings):
    settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode()
    token = encrypt_value("some-secret")

    settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode()  # different key
    with pytest.raises(InvalidToken):
        decrypt_value(token)


@pytest.mark.django_db
def test_str_representation_never_includes_secrets():
    config = TaraConfig.objects.create(
        name="Main", business_id="biz_1",
        api_key=encrypt_value("super-secret-key"), webhook_secret=encrypt_value("super-secret-whsec"),
    )
    rendered = str(config)
    assert "super-secret-key" not in rendered
    assert "super-secret-whsec" not in rendered
    assert config.api_key not in rendered  # ciphertext shouldn't appear either


# --- Singleton/active-configuration invariant ---

@pytest.mark.django_db
def test_activating_a_second_config_deactivates_the_first():
    first = TaraConfig.objects.create(name="First", business_id="b1", is_active=True)
    second = TaraConfig.objects.create(name="Second", business_id="b2", is_active=True)

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.is_active is False
    assert second.is_active is True


@pytest.mark.django_db
def test_db_level_constraint_blocks_two_active_rows_even_bypassing_save():
    """
    Defense in depth: even if application code bypasses TaraConfig.save()'s
    deactivation logic (e.g. via .update()), the database itself refuses two
    active rows — see Meta.constraints in apps/payments/models.py.
    """
    TaraConfig.objects.create(name="First", business_id="b1", is_active=True)
    second = TaraConfig.objects.create(name="Second", business_id="b2", is_active=False)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            TaraConfig.objects.filter(pk=second.pk).update(is_active=True)


# --- Legacy plaintext migration safety net ---

@pytest.mark.django_db
def test_legacy_plaintext_credentials_are_encrypted_in_place():
    config = TaraConfig.objects.create(name="Legacy", business_id="b1")
    # Simulate a pre-encryption row by writing plaintext directly, bypassing the service.
    TaraConfig.objects.filter(pk=config.pk).update(api_key="plaintext-key", webhook_secret="plaintext-secret")

    result = encrypt_legacy_plaintext_credentials(TaraConfig)

    config.refresh_from_db()
    assert result["migrated_count"] == 1
    assert config.api_key != "plaintext-key"
    assert decrypt_value(config.api_key) == "plaintext-key"
    assert decrypt_value(config.webhook_secret) == "plaintext-secret"


@pytest.mark.django_db
def test_legacy_migration_is_idempotent_and_never_double_encrypts():
    config = TaraConfig.objects.create(
        name="Already encrypted", business_id="b1",
        api_key=encrypt_value("already-encrypted-key"),
    )
    first_pass = encrypt_legacy_plaintext_credentials(TaraConfig)
    config.refresh_from_db()
    stored_after_first = config.api_key

    second_pass = encrypt_legacy_plaintext_credentials(TaraConfig)
    config.refresh_from_db()

    assert first_pass["migrated_count"] == 0
    assert second_pass["migrated_count"] == 0
    assert config.api_key == stored_after_first
    assert decrypt_value(config.api_key) == "already-encrypted-key"


@pytest.mark.django_db
def test_legacy_migration_handles_empty_and_mixed_rows_without_error():
    empty_row = TaraConfig.objects.create(name="No secrets yet", business_id="b1")
    plaintext_row = TaraConfig.objects.create(name="Legacy", business_id="b2")
    TaraConfig.objects.filter(pk=plaintext_row.pk).update(api_key="legacy-plaintext")
    encrypted_row = TaraConfig.objects.create(name="Modern", business_id="b3", api_key=encrypt_value("modern-key"))

    result = encrypt_legacy_plaintext_credentials(TaraConfig)

    assert result["migrated_count"] == 1
    empty_row.refresh_from_db()
    plaintext_row.refresh_from_db()
    encrypted_row.refresh_from_db()
    assert empty_row.api_key == ""
    assert decrypt_value(plaintext_row.api_key) == "legacy-plaintext"
    assert decrypt_value(encrypted_row.api_key) == "modern-key"
