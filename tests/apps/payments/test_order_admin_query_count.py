"""
Phase 8: OrderAdmin's changelist adds several computed display columns
(total paid, remaining balance, installment progress, access eligibility,
confirmation/provisioning state) that each iterate an order's related rows —
this proves that adding more orders to the page doesn't add proportionally
more queries (the get_queryset select_related/prefetch_related is working).
"""
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.services import OrderService
from apps.payments.models import PaymentPlan

pytestmark = pytest.mark.django_db

CHANGELIST_URL = reverse("admin:payments_order_changelist")


def make_orders(n, prefix):
    course = Course.objects.create(cf_course_id=f"crs_{prefix}", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code=f"plan_{prefix}", name="Bootcamp", course=course,
        installment_count=2, installment_amount=Decimal("50000.00"), is_active=True,
    )
    for i in range(n):
        contact = Contact.objects.create(email=f"{prefix}-{i}@example.com")
        OrderService().create_order(contact, plan.id, f"idem-{prefix}-{i}")


@pytest.fixture
def superuser_client(db):
    User.objects.create_superuser(username="queryadmin", email="qa@example.com", password="x")
    client = Client()
    client.login(username="queryadmin", password="x")
    return client


def test_changelist_query_count_does_not_scale_linearly_with_order_count(superuser_client):
    make_orders(3, "few")
    resp = superuser_client.get(CHANGELIST_URL)
    assert resp.status_code == 200

    from django.db import connection, reset_queries
    from django.test.utils import override_settings

    with override_settings(DEBUG=True):
        reset_queries()
        resp = superuser_client.get(CHANGELIST_URL)
        assert resp.status_code == 200
        queries_for_few = len(connection.queries)

        make_orders(15, "many")
        reset_queries()
        resp = superuser_client.get(CHANGELIST_URL)
        assert resp.status_code == 200
        queries_for_many = len(connection.queries)

    # 12 extra orders on the page must not add anywhere near 12x the query
    # count — a handful more (pagination/count queries) is fine; a per-row
    # N+1 would show up as a large jump.
    assert queries_for_many < queries_for_few + 10, (
        f"query count grew from {queries_for_few} to {queries_for_many} — looks like an N+1"
    )
