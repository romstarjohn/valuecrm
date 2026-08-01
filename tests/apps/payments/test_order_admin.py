from decimal import Decimal

import pytest
from django.urls import reverse

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import PaymentPlan
from apps.payments.services import OrderService

pytestmark = pytest.mark.django_db


@pytest.fixture
def order():
    course = Course.objects.create(cf_course_id="crs_1", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code="bootcamp-1x", name="Bootcamp", course=course, installment_count=1,
        installment_amount=Decimal("100000.00"), is_active=True,
    )
    contact = Contact.objects.create(email="buyer@example.com")
    created, _ = OrderService().create_order(contact, plan.id, "idem-admin-tests")
    return created


CHANGELIST_URL = reverse("admin:payments_order_changelist")


def change_url(order):
    return reverse("admin:payments_order_change", args=[order.pk])


# --- Authorization ---

def test_anonymous_cannot_view_orders(client, order):
    response = client.get(CHANGELIST_URL)
    assert response.status_code == 302
    assert "/admin/login/" in response.url


def test_staff_without_permissions_cannot_view_orders(staff_client, order):
    response = staff_client.get(CHANGELIST_URL)
    assert response.status_code == 403


def test_authorized_superuser_can_view_orders(superuser_client, order):
    response = superuser_client.get(CHANGELIST_URL)
    assert response.status_code == 200
    assert str(order.reference).encode() in response.content


def test_authorized_superuser_can_view_order_detail_with_installments(superuser_client, order):
    response = superuser_client.get(change_url(order))
    assert response.status_code == 200
    assert b"100000.00" in response.content or b"100,000.00" in response.content


# --- Read-only: no add/change/delete exposed in Phase 3 ---

def test_add_view_is_not_permitted(superuser_client):
    response = superuser_client.get(reverse("admin:payments_order_add"))
    assert response.status_code == 403


def test_change_view_does_not_accept_writes(superuser_client, order):
    original_status = order.status
    response = superuser_client.post(change_url(order), data={"status": "CANCELLED"})
    assert response.status_code == 403
    order.refresh_from_db()
    assert order.status == original_status


def test_delete_view_is_not_permitted(superuser_client, order):
    response = superuser_client.get(reverse("admin:payments_order_delete", args=[order.pk]))
    assert response.status_code == 403
