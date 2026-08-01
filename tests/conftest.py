import pytest
from django.contrib.auth.models import User
from django.test import Client

@pytest.fixture(autouse=True)
def enable_db_access_for_all_tests(db):
    pass

@pytest.fixture(autouse=True)
def default_public_base_url(settings):
    """
    apps.payments checkout (Phase 5) fails closed if PUBLIC_BASE_URL is unset
    (see CheckoutService._require_public_base_url). A placeholder HTTPS value
    here is harmless for every test that doesn't touch checkout, and lets
    checkout tests exercise the real code path without needing a live domain
    — no test ever actually calls Tara for real (all mocked), so nothing
    connects to this host.
    """
    settings.PUBLIC_BASE_URL = "https://checkout.example.com"

@pytest.fixture(autouse=True)
def disable_rate_limiting_by_default(settings):
    """
    django-ratelimit (apps/payments/views.py) uses Django's cache framework,
    which — unlike the database — is NOT rolled back between tests, so a
    shared "ip" key would accumulate across every test in a run and make
    unrelated tests flaky once the configured rate is exceeded. Disabled by
    default here (the library's own documented RATELIMIT_ENABLE switch);
    tests that specifically verify rate-limiting behavior re-enable it via
    the `settings` fixture for just that test.
    """
    settings.RATELIMIT_ENABLE = False

@pytest.fixture
def staff_user(db):
    return User.objects.create_user(
        username="staff", 
        password="password", 
        is_staff=True
    )

@pytest.fixture
def staff_client(staff_user):
    client = Client()
    client.login(username="staff", password="password")
    return client

@pytest.fixture
def superuser(db):
    return User.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="password",
    )

@pytest.fixture
def superuser_client(superuser):
    client = Client()
    client.login(username="admin", password="password")
    return client
