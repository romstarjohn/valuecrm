"""
Phase 3 security hotfix: apps/configuration/admin.py::ClickFunnelsConfigForm had
the identical Meta.fields bug fixed for TaraConfigForm in Phase 2 — a blank
api_access_token on any admin edit silently wiped the existing encrypted token.
Mirrors tests/apps/payments/test_tara_config_admin.py's structure.
"""
import pytest
from django.contrib.admin.models import LogEntry
from django.urls import reverse

from apps.configuration.models import ClickFunnelsConfig
from shared.security import decrypt_value, encrypt_value


@pytest.fixture
def existing_config(db):
    return ClickFunnelsConfig.objects.create(
        name="Main", api_user_agent="ValuedCRM/1.0",
        api_access_token=encrypt_value("original-cf-token"),
    )


def change_url(config):
    return reverse("admin:configuration_clickfunnelsconfig_change", args=[config.pk])


@pytest.mark.django_db
def test_blank_token_preserves_existing_credential(superuser_client, existing_config):
    response = superuser_client.post(change_url(existing_config), data={
        "name": "Main (renamed)", "is_active": "", "api_user_agent": "ValuedCRM/1.0", "api_access_token": "",
    }, follow=True)
    assert response.status_code == 200

    existing_config.refresh_from_db()
    assert existing_config.name == "Main (renamed)"
    assert decrypt_value(existing_config.api_access_token) == "original-cf-token"


@pytest.mark.django_db
def test_explicit_token_replacement_works(superuser_client, existing_config):
    response = superuser_client.post(change_url(existing_config), data={
        "name": "Main", "is_active": "", "api_user_agent": "ValuedCRM/1.0", "api_access_token": "brand-new-token",
    }, follow=True)
    assert response.status_code == 200

    existing_config.refresh_from_db()
    assert decrypt_value(existing_config.api_access_token) == "brand-new-token"


@pytest.mark.django_db
def test_decrypted_token_never_appears_in_rendered_html(superuser_client, existing_config):
    response = superuser_client.get(change_url(existing_config))
    assert response.status_code == 200
    assert b"original-cf-token" not in response.content
    assert existing_config.api_access_token.encode() not in response.content  # ciphertext absent too
    assert b'name="api_access_token" value=' not in response.content


@pytest.mark.django_db
def test_unauthorized_users_cannot_modify_configuration(client, staff_client, existing_config):
    anon_response = client.post(change_url(existing_config), data={
        "name": "Hijacked", "is_active": "", "api_user_agent": "x", "api_access_token": "stolen-token",
    })
    assert anon_response.status_code == 302
    assert "/admin/login/" in anon_response.url

    staff_response = staff_client.post(change_url(existing_config), data={
        "name": "Hijacked", "is_active": "", "api_user_agent": "x", "api_access_token": "stolen-token",
    })
    assert staff_response.status_code == 403

    existing_config.refresh_from_db()
    assert existing_config.name == "Main"
    assert decrypt_value(existing_config.api_access_token) == "original-cf-token"


@pytest.mark.django_db
def test_no_token_in_logs_or_log_entry(superuser_client, existing_config, caplog):
    superuser_client.post(change_url(existing_config), data={
        "name": "Main (renamed)", "is_active": "", "api_user_agent": "ValuedCRM/1.0", "api_access_token": "brand-new-token",
    }, follow=True)

    entry = LogEntry.objects.filter(object_id=str(existing_config.pk)).latest("action_time")
    assert "brand-new-token" not in entry.change_message
    assert "original-cf-token" not in entry.change_message

    for record in caplog.records:
        message = record.getMessage()
        assert "brand-new-token" not in message
        assert "original-cf-token" not in message
