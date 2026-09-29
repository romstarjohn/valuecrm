"""
Tara settings page — separate sibling of apps/configuration/views.py::settings_view
(ClickFunnels), same @login_required rule, same write-only-secret ModelForm
shape. Mirrors tests/apps/configuration/test_config_views.py's conventions.
"""
import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.configuration.models import ClickFunnelsConfig
from apps.payments.models import TaraConfig
from shared.security import decrypt_value, encrypt_value

TARA_URL_NAME = "configuration:tara_settings"
CF_URL_NAME = "configuration:settings"


@pytest.fixture
def active_tara_config(db):
    return TaraConfig.objects.create(
        name="Main", business_id="biz_123", is_active=True,
        api_key=encrypt_value("original-api-key"),
        webhook_secret=encrypt_value("original-webhook-secret"),
    )


# --- Separate route/page ---

def test_tara_has_a_separate_route():
    url = reverse(TARA_URL_NAME)
    assert url == "/settings/tara/", url
    assert url != reverse(CF_URL_NAME)


@pytest.mark.django_db
def test_tara_page_renders_its_own_template(superuser_client, active_tara_config):
    response = superuser_client.get(reverse(TARA_URL_NAME))
    assert response.status_code == 200
    assert [t.name for t in response.templates if t.name] and "configuration/tara_settings.html" in [t.name for t in response.templates]


@pytest.mark.django_db
def test_clickfunnels_page_still_uses_its_own_template(superuser_client):
    response = superuser_client.get(reverse(CF_URL_NAME))
    assert response.status_code == 200
    assert "configuration/settings.html" in [t.name for t in response.templates if t.name]


# --- Navigation ---

@pytest.mark.django_db
def test_navigation_exposes_tara_link(superuser_client):
    response = superuser_client.get(reverse("dashboard:index"))
    content = response.content.decode()
    assert reverse(TARA_URL_NAME) in content
    assert "Connexion Tara" in content


def _extract_anchor_tag(content: str, href: str) -> str:
    """Returns the full opening <a ...> tag whose href matches, regardless of attribute order."""
    href_pos = content.index(f'href="{href}"')
    tag_start = content.rfind("<a", 0, href_pos)
    tag_end = content.index(">", href_pos)
    return content[tag_start:tag_end + 1]


@pytest.mark.django_db
def test_active_state_highlights_only_tara_on_tara_page(superuser_client):
    response = superuser_client.get(reverse(TARA_URL_NAME))
    content = response.content.decode()
    tara_tag = _extract_anchor_tag(content, reverse(TARA_URL_NAME))
    cf_tag = _extract_anchor_tag(content, reverse(CF_URL_NAME))
    assert "active" in tara_tag
    assert "active" not in cf_tag


@pytest.mark.django_db
def test_active_state_highlights_only_clickfunnels_on_clickfunnels_page(superuser_client):
    response = superuser_client.get(reverse(CF_URL_NAME))
    content = response.content.decode()
    tara_tag = _extract_anchor_tag(content, reverse(TARA_URL_NAME))
    cf_tag = _extract_anchor_tag(content, reverse(CF_URL_NAME))
    assert "active" not in tara_tag
    assert "active" in cf_tag


# --- Authorization ---

def test_anonymous_get_redirects_to_login(client):
    response = client.get(reverse(TARA_URL_NAME))
    assert response.status_code == 302
    assert "/connexion/" in response.url


@pytest.mark.django_db
def test_anonymous_post_produces_no_configuration_change(client, active_tara_config):
    response = client.post(reverse(TARA_URL_NAME), data={
        "name": "Hacked", "business_id": "hacked_biz", "is_active": "on",
        "api_key": "attacker-key", "webhook_secret": "attacker-secret",
    })
    assert response.status_code == 302
    assert "/connexion/" in response.url
    active_tara_config.refresh_from_db()
    assert active_tara_config.name == "Main"
    assert active_tara_config.business_id == "biz_123"
    assert decrypt_value(active_tara_config.api_key) == "original-api-key"


@pytest.mark.django_db
def test_connection_pages_are_admin_only():
    """
    Whoever can edit the Tara/ClickFunnels credentials can redirect or break
    every payment — both pages (and the ClickFunnels verify action) are
    administrator-only. Staff get a plain "ask an administrator" page, and
    nothing is saved on POST.
    """
    User.objects.create_user(username="plainuser", password="x", is_staff=True)
    client = Client()
    client.login(username="plainuser", password="x")

    for name in (TARA_URL_NAME, "configuration:settings"):
        response = client.get(reverse(name))
        assert response.status_code == 403
        assert "Réservé aux administrateurs" in response.content.decode()

    response = client.post(reverse(TARA_URL_NAME), {"name": "Hijack", "business_id": "evil", "api_key": "k", "webhook_secret": "s"})
    assert response.status_code == 403
    assert not TaraConfig.objects.filter(business_id="evil").exists()
    assert client.post(reverse("configuration:verify")).status_code == 403


@pytest.mark.django_db
def test_explicit_permission_grants_access_without_superuser():
    from django.contrib.auth.models import Permission

    user = User.objects.create_user(username="finance", password="x", is_staff=True)
    user.user_permissions.add(Permission.objects.get(codename="change_taraconfig"))
    client = Client()
    client.login(username="finance", password="x")

    assert client.get(reverse(TARA_URL_NAME)).status_code == 200
    assert client.get(reverse("configuration:settings")).status_code == 403  # separate permission


@pytest.mark.django_db
def test_sidebar_hides_connection_pages_from_staff():
    User.objects.create_user(username="plain2", password="x", is_staff=True)
    client = Client()
    client.login(username="plain2", password="x")
    page = client.get(reverse("dashboard:index")).content.decode()
    assert "Connexion Tara" not in page and "Connexion ClickFunnels" not in page


@pytest.mark.django_db
def test_authorized_get_succeeds(superuser_client):
    response = superuser_client.get(reverse(TARA_URL_NAME))
    assert response.status_code == 200


# --- Configuration creation / update ---

@pytest.mark.django_db
def test_authorized_first_time_configuration_creation(superuser_client):
    response = superuser_client.post(reverse(TARA_URL_NAME), data={
        "name": "Main", "business_id": "biz_new", "is_active": "on",
        "api_key": "new-api-key", "webhook_secret": "new-webhook-secret",
    }, follow=True)
    assert response.status_code == 200
    config = TaraConfig.objects.get(business_id="biz_new")
    assert decrypt_value(config.api_key) == "new-api-key"
    assert decrypt_value(config.webhook_secret) == "new-webhook-secret"
    assert config.is_active is True


@pytest.mark.django_db
def test_first_time_configuration_requires_secrets(superuser_client):
    response = superuser_client.post(reverse(TARA_URL_NAME), data={
        "name": "Main", "business_id": "biz_new", "is_active": "on",
        "api_key": "", "webhook_secret": "",
    })
    assert response.status_code == 200  # re-rendered with errors, not redirected
    assert TaraConfig.objects.filter(business_id="biz_new").count() == 0


@pytest.mark.django_db
def test_business_id_update(superuser_client, active_tara_config):
    superuser_client.post(reverse(TARA_URL_NAME), data={
        "name": "Main", "business_id": "biz_updated", "is_active": "on",
        "api_key": "", "webhook_secret": "",
    })
    active_tara_config.refresh_from_db()
    assert active_tara_config.business_id == "biz_updated"


@pytest.mark.django_db
def test_missing_business_id_rejected(superuser_client, active_tara_config):
    response = superuser_client.post(reverse(TARA_URL_NAME), data={
        "name": "Main", "business_id": "", "is_active": "on",
        "api_key": "", "webhook_secret": "",
    })
    assert response.status_code == 200  # re-rendered with errors, not redirected
    assert response.context["form"].errors.get("business_id")
    active_tara_config.refresh_from_db()
    assert active_tara_config.business_id == "biz_123"  # unchanged


@pytest.mark.django_db
def test_whitespace_only_business_id_rejected(superuser_client, active_tara_config):
    """
    Django's CharField normalizes (strips) '   ' to '' before the base
    required check runs, so this hits the same required-field error as a
    fully empty submission; clean_business_id's own .strip() is defense in
    depth for any future required=False relaxation.
    """
    response = superuser_client.post(reverse(TARA_URL_NAME), data={
        "name": "Main", "business_id": "   ", "is_active": "on",
        "api_key": "", "webhook_secret": "",
    })
    assert response.status_code == 200
    assert response.context["form"].errors.get("business_id")
    active_tara_config.refresh_from_db()
    assert active_tara_config.business_id == "biz_123"


@pytest.mark.django_db
def test_enable_disable(superuser_client, active_tara_config):
    superuser_client.post(reverse(TARA_URL_NAME), data={
        "name": "Main", "business_id": "biz_123",
        "api_key": "", "webhook_secret": "",
    })  # is_active omitted -> unchecked
    active_tara_config.refresh_from_db()
    assert active_tara_config.is_active is False

    superuser_client.post(reverse(TARA_URL_NAME), data={
        "name": "Main", "business_id": "biz_123", "is_active": "on",
        "api_key": "", "webhook_secret": "",
    })
    active_tara_config.refresh_from_db()
    assert active_tara_config.is_active is True


@pytest.mark.django_db
def test_single_active_config_invariant_preserved(superuser_client, active_tara_config):
    """
    The view saves via TaraConfig.save() (apps/payments/models.py), which
    already atomically deactivates any other active row — this just proves
    the view's save path still results in exactly one active config.
    """
    superuser_client.post(reverse(TARA_URL_NAME), data={
        "name": "Main", "business_id": "biz_123", "is_active": "on",
        "api_key": "", "webhook_secret": "",
    })
    assert TaraConfig.objects.filter(is_active=True).count() == 1


# --- Secret handling ---

@pytest.mark.django_db
def test_existing_api_key_absent_from_html(superuser_client, active_tara_config):
    response = superuser_client.get(reverse(TARA_URL_NAME))
    assert "original-api-key" not in response.content.decode()


@pytest.mark.django_db
def test_existing_webhook_secret_absent_from_html(superuser_client, active_tara_config):
    response = superuser_client.get(reverse(TARA_URL_NAME))
    assert "original-webhook-secret" not in response.content.decode()


@pytest.mark.django_db
def test_secret_ciphertext_absent_from_html(superuser_client, active_tara_config):
    response = superuser_client.get(reverse(TARA_URL_NAME))
    content = response.content.decode()
    assert active_tara_config.api_key not in content
    assert active_tara_config.webhook_secret not in content


@pytest.mark.django_db
def test_blank_fields_preserve_existing_secrets(superuser_client, active_tara_config):
    superuser_client.post(reverse(TARA_URL_NAME), data={
        "name": "Main (renamed)", "business_id": "biz_123", "is_active": "on",
        "api_key": "", "webhook_secret": "",
    })
    active_tara_config.refresh_from_db()
    assert active_tara_config.name == "Main (renamed)"
    assert decrypt_value(active_tara_config.api_key) == "original-api-key"
    assert decrypt_value(active_tara_config.webhook_secret) == "original-webhook-secret"


@pytest.mark.django_db
def test_explicit_api_key_replacement(superuser_client, active_tara_config):
    superuser_client.post(reverse(TARA_URL_NAME), data={
        "name": "Main", "business_id": "biz_123", "is_active": "on",
        "api_key": "replacement-api-key", "webhook_secret": "",
    })
    active_tara_config.refresh_from_db()
    assert decrypt_value(active_tara_config.api_key) == "replacement-api-key"
    assert decrypt_value(active_tara_config.webhook_secret) == "original-webhook-secret"


@pytest.mark.django_db
def test_explicit_webhook_secret_replacement_preserves_api_key(superuser_client, active_tara_config):
    superuser_client.post(reverse(TARA_URL_NAME), data={
        "name": "Main", "business_id": "biz_123", "is_active": "on",
        "api_key": "", "webhook_secret": "replacement-webhook-secret",
    })
    active_tara_config.refresh_from_db()
    assert decrypt_value(active_tara_config.webhook_secret) == "replacement-webhook-secret"
    assert decrypt_value(active_tara_config.api_key) == "original-api-key"


@pytest.mark.django_db
def test_invalid_submission_preserves_stored_credentials(superuser_client, active_tara_config):
    response = superuser_client.post(reverse(TARA_URL_NAME), data={
        "name": "Main", "business_id": "",  # invalid — required
        "is_active": "on", "api_key": "should-not-be-saved", "webhook_secret": "should-not-be-saved-either",
    })
    assert response.status_code == 200
    active_tara_config.refresh_from_db()
    assert decrypt_value(active_tara_config.api_key) == "original-api-key"
    assert decrypt_value(active_tara_config.webhook_secret) == "original-webhook-secret"


@pytest.mark.django_db
def test_csrf_enforced(active_tara_config):
    User.objects.create_user(username="csrfuser", password="x", is_staff=True)
    client = Client(enforce_csrf_checks=True)
    client.login(username="csrfuser", password="x")
    response = client.post(reverse(TARA_URL_NAME), data={
        "name": "Main", "business_id": "biz_123", "is_active": "on",
        "api_key": "", "webhook_secret": "",
    })
    assert response.status_code == 403


# --- Webhook URL ---

@pytest.mark.django_db
def test_webhook_url_uses_public_base_url(superuser_client, active_tara_config, settings):
    settings.PUBLIC_BASE_URL = "https://checkout.example.com"
    response = superuser_client.get(reverse(TARA_URL_NAME))
    assert "https://checkout.example.com/api/tara/webhook/" in response.content.decode()


@pytest.mark.django_db
def test_webhook_url_ignores_request_host(superuser_client, active_tara_config, settings):
    settings.PUBLIC_BASE_URL = "https://checkout.example.com"
    settings.ALLOWED_HOSTS = ["attacker.example.org", "testserver"]
    response = superuser_client.get(reverse(TARA_URL_NAME), HTTP_HOST="attacker.example.org")
    content = response.content.decode()
    assert "https://checkout.example.com/api/tara/webhook/" in content
    assert "attacker.example.org" not in content


# --- No secret leakage ---

@pytest.mark.django_db
def test_no_secret_in_messages(superuser_client, active_tara_config):
    response = superuser_client.post(reverse(TARA_URL_NAME), data={
        "name": "Main", "business_id": "biz_123", "is_active": "on",
        "api_key": "brand-new-secret-value", "webhook_secret": "",
    }, follow=True)
    assert "brand-new-secret-value" not in response.content.decode()


@pytest.mark.django_db
def test_no_secret_in_template_context(superuser_client, active_tara_config):
    response = superuser_client.get(reverse(TARA_URL_NAME))
    form = response.context["form"]
    assert form.initial.get("api_key") != "original-api-key"
    assert form.initial.get("webhook_secret") != "original-webhook-secret"
    assert not form.initial.get("api_key")
    assert not form.initial.get("webhook_secret")


@pytest.mark.django_db
def test_no_secret_in_logs(superuser_client, active_tara_config, caplog):
    import logging
    caplog.set_level(logging.INFO)
    superuser_client.post(reverse(TARA_URL_NAME), data={
        "name": "Main", "business_id": "biz_123", "is_active": "on",
        "api_key": "log-sensitive-value", "webhook_secret": "",
    })
    for record in caplog.records:
        assert "log-sensitive-value" not in record.getMessage()


# --- Plain-language status (docs/UI_VOCABULARY.md) ---

@pytest.mark.django_db
def test_tara_page_says_what_to_do_when_not_connected(superuser_client):
    import html
    content = html.unescape(superuser_client.get(reverse(TARA_URL_NAME)).content.decode())
    assert "Connexion Tara" in content
    assert "Non connecté" in content
    assert "Que faire ?" in content
    assert "À quoi ça sert ?" in content


@pytest.mark.django_db
def test_tara_page_shows_connected_and_last_notification(superuser_client, active_tara_config):
    from apps.payments.models import TaraWebhookEvent
    TaraWebhookEvent.objects.create(dedup_key="n1", raw_provider_status="SUCCESS", tara_payment_id="p1")
    content = superuser_client.get(reverse(TARA_URL_NAME)).content.decode()
    assert "Connecté ✓" in content
    assert "Dernière notification reçue de Tara" in content
