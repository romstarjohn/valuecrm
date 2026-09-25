import logging

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import Client, RequestFactory

from shared.ratelimit import client_ip

pytestmark = pytest.mark.django_db


@pytest.fixture
def rate_limiting(settings):
    settings.RATELIMIT_ENABLE = True
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def user():
    return User.objects.create_user(username="ops", password="correct-password", is_staff=True)


def post_login(client, username="ops", password="wrong", **extra):
    return client.post("/connexion/", {"username": username, "password": password}, **extra)


# --- Login ---

def test_wrong_credentials_rerender_login_with_message(user):
    response = post_login(Client())
    assert response.status_code == 200
    assert "Identifiants incorrects" in response.content.decode()


def test_failed_login_is_logged_without_password(user, caplog):
    with caplog.at_level(logging.WARNING, logger="security.auth"):
        post_login(Client(), password="super-secret-guess")
    assert "Failed login" in caplog.text
    assert "username=ops" in caplog.text
    assert "super-secret-guess" not in caplog.text


def test_login_blocked_per_username_after_limit(user, rate_limiting):
    client = Client()
    for i in range(5):
        # vary the IP so only the per-username limit can trigger
        post_login(client, REMOTE_ADDR=f"10.0.0.{i + 1}")
    response = post_login(client, password="correct-password", REMOTE_ADDR="10.0.0.99")

    assert response.status_code == 429
    assert "Trop de tentatives" in response.content.decode()
    assert "_auth_user_id" not in client.session  # correct password is not even checked while limited


def test_login_blocked_per_ip_after_limit(user, rate_limiting):
    client = Client()
    for i in range(10):
        post_login(client, username=f"user{i}", REMOTE_ADDR="10.1.1.1")
    assert post_login(client, username="other", REMOTE_ADDR="10.1.1.1").status_code == 429
    assert post_login(client, username="other2", REMOTE_ADDR="10.1.1.2").status_code == 200


def test_successful_login_still_works(user, rate_limiting):
    client = Client()
    response = post_login(client, password="correct-password")
    assert response.status_code == 302
    assert "_auth_user_id" in client.session


# --- Client IP behind a proxy ---

def test_client_ip_ignores_forwarded_header_without_trusted_proxy(settings):
    settings.TRUSTED_PROXY_COUNT = 0
    request = RequestFactory().get("/", REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="203.0.113.7")
    assert client_ip(request) == "127.0.0.1"


def test_client_ip_uses_rightmost_entry_appended_by_trusted_proxy(settings):
    settings.TRUSTED_PROXY_COUNT = 1
    # "6.6.6.6" was sent by the client itself (spoofed), Apache appended the real one
    request = RequestFactory().get("/", REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="6.6.6.6, 203.0.113.7")
    assert client_ip(request) == "203.0.113.7"


def test_client_ip_falls_back_on_invalid_forwarded_value(settings):
    settings.TRUSTED_PROXY_COUNT = 1
    request = RequestFactory().get("/", REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="not-an-ip")
    assert client_ip(request) == "127.0.0.1"


# --- Error pages ---

def test_404_page_is_branded_french(settings):
    settings.DEBUG = False
    response = Client().get("/this-page-does-not-exist/")
    assert response.status_code == 404
    assert "Page introuvable" in response.content.decode()


def test_ratelimited_checkout_returns_429_page(settings, rate_limiting):
    settings.DEBUG = False
    client = Client()
    statuses = [client.get("/paiement/").status_code for _ in range(21)]
    assert statuses[-1] == 429
    assert "Trop de demandes" in client.get("/paiement/").content.decode()


def test_csrf_failure_page_is_french(user):
    response = Client(enforce_csrf_checks=True).post("/connexion/", {"username": "ops", "password": "x"})
    assert response.status_code == 403
    assert "Votre session a expiré" in response.content.decode()


def test_500_template_renders_with_empty_context():
    """Django renders 500.html without a request or context processors — it must not depend on either."""
    html = render_to_string("500.html")
    assert "Une erreur technique est survenue" in html
