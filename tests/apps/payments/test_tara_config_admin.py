import pytest
from django.contrib.admin.models import LogEntry
from django.urls import reverse

from apps.payments.models import TaraConfig
from shared.security import decrypt_value, encrypt_value

ADD_URL = reverse("admin:payments_taraconfig_add")
CHANGELIST_URL = reverse("admin:payments_taraconfig_changelist")


def valid_payload(**overrides):
    payload = {
        "name": "Main",
        "business_id": "biz_123",
        "is_active": "on",
        "api_key": "raw-api-key",
        "webhook_secret": "raw-webhook-secret",
    }
    payload.update(overrides)
    return payload


# --- Authorization ---

@pytest.mark.django_db
def test_authorized_admin_can_create_config(superuser_client):
    response = superuser_client.post(ADD_URL, data=valid_payload(), follow=True)
    assert response.status_code == 200
    config = TaraConfig.objects.get(name="Main")
    assert config.business_id == "biz_123"
    assert decrypt_value(config.api_key) == "raw-api-key"
    assert decrypt_value(config.webhook_secret) == "raw-webhook-secret"


@pytest.mark.django_db
def test_anonymous_user_cannot_create_config(client):
    response = client.post(ADD_URL, data=valid_payload())
    assert response.status_code == 302
    assert "/admin/login/" in response.url
    assert not TaraConfig.objects.exists()


@pytest.mark.django_db
def test_staff_without_permissions_cannot_create_config(staff_client):
    response = staff_client.post(ADD_URL, data=valid_payload())
    assert response.status_code == 302
    assert not TaraConfig.objects.exists()


@pytest.mark.django_db
def test_creation_is_audited_without_secret_values(superuser_client):
    superuser_client.post(ADD_URL, data=valid_payload(), follow=True)
    config = TaraConfig.objects.get(name="Main")
    entry = LogEntry.objects.get(object_id=str(config.pk))
    assert entry.action_flag == 1  # ADDITION
    assert "raw-api-key" not in entry.change_message
    assert "raw-webhook-secret" not in entry.change_message
    assert config.api_key not in entry.change_message


# --- Secrets never exposed in rendered HTML/responses ---

@pytest.mark.django_db
def test_secrets_absent_from_add_form_html(superuser_client):
    response = superuser_client.get(ADD_URL)
    assert response.status_code == 200
    assert b"raw-api-key" not in response.content


@pytest.mark.django_db
def test_secrets_absent_from_change_form_html(superuser_client):
    config = TaraConfig.objects.create(
        name="Main", business_id="biz_123",
        api_key=encrypt_value("raw-api-key"), webhook_secret=encrypt_value("raw-webhook-secret"),
    )
    change_url = reverse("admin:payments_taraconfig_change", args=[config.pk])
    response = superuser_client.get(change_url)
    assert response.status_code == 200
    assert b"raw-api-key" not in response.content
    assert b"raw-webhook-secret" not in response.content
    assert config.api_key.encode() not in response.content  # ciphertext absent too
    assert config.webhook_secret.encode() not in response.content
    # PasswordInput fields must render empty, never pre-filled
    assert b'name="api_key"' in response.content
    assert b'name="api_key" value=' not in response.content


@pytest.mark.django_db
def test_secrets_absent_from_changelist_html(superuser_client):
    TaraConfig.objects.create(
        name="Main", business_id="biz_123",
        api_key=encrypt_value("raw-api-key"), webhook_secret=encrypt_value("raw-webhook-secret"),
    )
    response = superuser_client.get(CHANGELIST_URL)
    assert response.status_code == 200
    assert b"raw-api-key" not in response.content
    assert b"raw-webhook-secret" not in response.content


@pytest.mark.django_db
def test_masking_shows_presence_not_value(superuser_client):
    TaraConfig.objects.create(name="Configured", business_id="b1", api_key=encrypt_value("k"), webhook_secret="")
    TaraConfig.objects.create(name="Empty", business_id="b2")
    response = superuser_client.get(CHANGELIST_URL)
    # Django's boolean admin.display renders a yes/no icon per row — presence-only, never the value.
    assert b"field-api_key_configured" in response.content
    assert b"field-webhook_secret_configured" in response.content
    assert b"icon-yes" in response.content  # "Configured" row's api_key
    assert b"icon-no" in response.content   # "Empty" row's api_key / "Configured" row's webhook_secret


# --- Blank preserves / explicit replacement (full HTTP round trip) ---

@pytest.mark.django_db
def test_blank_secret_fields_preserve_existing_credentials_via_http(superuser_client):
    config = TaraConfig.objects.create(
        name="Main", business_id="biz_123",
        api_key=encrypt_value("original-key"), webhook_secret=encrypt_value("original-secret"),
    )
    change_url = reverse("admin:payments_taraconfig_change", args=[config.pk])

    response = superuser_client.post(change_url, data={
        "name": "Main (renamed)", "business_id": "biz_123", "is_active": "",
        "api_key": "", "webhook_secret": "",
    }, follow=True)
    assert response.status_code == 200

    config.refresh_from_db()
    assert config.name == "Main (renamed)"
    assert decrypt_value(config.api_key) == "original-key"
    assert decrypt_value(config.webhook_secret) == "original-secret"


@pytest.mark.django_db
def test_explicit_replacement_via_http(superuser_client):
    config = TaraConfig.objects.create(
        name="Main", business_id="biz_123",
        api_key=encrypt_value("original-key"), webhook_secret=encrypt_value("original-secret"),
    )
    change_url = reverse("admin:payments_taraconfig_change", args=[config.pk])

    response = superuser_client.post(change_url, data={
        "name": "Main", "business_id": "biz_123", "is_active": "",
        "api_key": "brand-new-key", "webhook_secret": "",
    }, follow=True)
    assert response.status_code == 200

    config.refresh_from_db()
    assert decrypt_value(config.api_key) == "brand-new-key"
    assert decrypt_value(config.webhook_secret) == "original-secret"  # untouched


# --- Enable/disable + singleton via admin ---

@pytest.mark.django_db
def test_enable_disable_via_admin(superuser_client):
    superuser_client.post(ADD_URL, data=valid_payload(is_active=""), follow=True)
    config = TaraConfig.objects.get(name="Main")
    assert config.is_active is False

    change_url = reverse("admin:payments_taraconfig_change", args=[config.pk])
    superuser_client.post(change_url, data={
        "name": "Main", "business_id": "biz_123", "is_active": "on",
        "api_key": "", "webhook_secret": "",
    }, follow=True)
    config.refresh_from_db()
    assert config.is_active is True


@pytest.mark.django_db
def test_activating_second_config_via_admin_deactivates_first(superuser_client):
    superuser_client.post(ADD_URL, data=valid_payload(name="First", business_id="b1"), follow=True)
    superuser_client.post(ADD_URL, data=valid_payload(name="Second", business_id="b2"), follow=True)

    first = TaraConfig.objects.get(name="First")
    second = TaraConfig.objects.get(name="Second")
    assert first.is_active is False
    assert second.is_active is True


# --- business_id: configurable, not treated as a secret ---

@pytest.mark.django_db
def test_business_id_is_visible_not_masked(superuser_client):
    superuser_client.post(ADD_URL, data=valid_payload(business_id="biz_visible_123"), follow=True)
    response = superuser_client.get(CHANGELIST_URL)
    assert b"biz_visible_123" in response.content


# --- No credential-verification action is exposed this phase ---

@pytest.mark.django_db
def test_no_verification_action_is_registered(superuser_client):
    response = superuser_client.get(CHANGELIST_URL)
    assert b"Verify Tara Credentials" not in response.content


@pytest.mark.django_db
def test_new_config_stays_pending_unverified(superuser_client):
    superuser_client.post(ADD_URL, data=valid_payload(), follow=True)
    config = TaraConfig.objects.get(name="Main")
    assert config.validation_status == TaraConfig.ValidationStatus.PENDING
