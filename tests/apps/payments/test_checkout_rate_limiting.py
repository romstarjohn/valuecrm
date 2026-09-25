"""
django-ratelimit is disabled by default across the test suite (see
tests/conftest.py::disable_rate_limiting_by_default) because its cache-backed
state isn't rolled back between tests like the database is. This file
re-enables it explicitly to prove the decorators on apps/payments/views.py
actually block excess requests, isolated from every other test.
"""
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.urls import reverse

from apps.courses.models import Course
from apps.payments.models import PaymentPlan

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_ratelimit_cache():
    cache.clear()
    yield
    cache.clear()


def test_checkout_start_is_rate_limited_per_ip(client, settings):
    settings.RATELIMIT_ENABLE = True
    course = Course.objects.create(cf_course_id="crs_rl", name="Bootcamp", workspace_id="ws_1")
    PaymentPlan.objects.create(
        code="rl-plan", name="RL Plan", course=course,
        installment_count=1, installment_amount=Decimal("1.00"), is_active=True,
    )
    url = reverse("payments:checkout_start", args=[course.slug])

    statuses = [client.get(url).status_code for _ in range(25)]
    assert 429 in statuses or 403 in statuses, "expected at least one rate-limited response among 25 rapid requests"


def test_shop_index_is_rate_limited_per_ip(client, settings):
    settings.RATELIMIT_ENABLE = True
    url = reverse("payments:shop_index")

    statuses = [client.get(url).status_code for _ in range(25)]
    assert 429 in statuses or 403 in statuses, "expected at least one rate-limited response among 25 rapid requests"
