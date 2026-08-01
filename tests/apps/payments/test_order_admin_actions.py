from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission, User
from django.test import Client
from django.urls import reverse

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import AdminAuditLog, Order, PaymentAttempt, PaymentPlan
from apps.payments.services import OrderService, PaymentAttemptService, PaymentCreditService

pytestmark = pytest.mark.django_db

CHANGELIST_URL = reverse("admin:payments_order_changelist")


def make_order(email="order-admin@example.com", installment_count=1, installment_amount=Decimal("100000.00")):
    course = Course.objects.create(cf_course_id=f"crs_{email}", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code=f"plan_{email}", name="Bootcamp", course=course,
        installment_count=installment_count, installment_amount=installment_amount, is_active=True,
    )
    contact = Contact.objects.create(email=email)
    order, _ = OrderService().create_order(contact, plan.id, f"idem-{email}")
    return order


@pytest.fixture
def order(db):
    return make_order()


@pytest.fixture
def superuser_client(db):
    User.objects.create_superuser(username="orderadmin", email="oa@example.com", password="x")
    client = Client()
    client.login(username="orderadmin", password="x")
    return client


def staff_client_with_perms(*codenames):
    user = User.objects.create_user(username=f"staff-{'-'.join(codenames)}"[:150], password="x", is_staff=True)
    user.user_permissions.add(Permission.objects.get(codename="view_order"))
    for codename in codenames:
        user.user_permissions.add(Permission.objects.get(codename=codename))
    client = Client()
    client.login(username=user.username, password="x")
    return client, user


# --- Authorization ---

def test_anonymous_cannot_access_changelist(client, order):
    resp = client.get(CHANGELIST_URL)
    assert resp.status_code == 302
    assert "/admin/login/" in resp.url


def test_staff_without_cancel_permission_gets_403(order):
    client, _ = staff_client_with_perms()  # view only
    resp = client.post(CHANGELIST_URL, {
        "action": "cancel_order_action", "_selected_action": [str(order.pk)],
        "confirm_apply": "1", "reason": "trying anyway",
    })
    assert resp.status_code == 403
    order.refresh_from_db()
    assert order.status == Order.Status.PENDING
    assert AdminAuditLog.objects.count() == 0


def test_staff_with_permission_can_cancel(order):
    client, _ = staff_client_with_perms("cancel_order")
    resp = client.post(CHANGELIST_URL, {
        "action": "cancel_order_action", "_selected_action": [str(order.pk)],
        "confirm_apply": "1", "reason": "authorized cancellation",
    }, follow=True)
    assert resp.status_code == 200
    order.refresh_from_db()
    assert order.status == Order.Status.CANCELLED


def test_superuser_has_implicit_access(superuser_client, order):
    resp = superuser_client.post(CHANGELIST_URL, {
        "action": "cancel_order_action", "_selected_action": [str(order.pk)],
        "confirm_apply": "1", "reason": "superuser cancel",
    }, follow=True)
    assert resp.status_code == 200
    order.refresh_from_db()
    assert order.status == Order.Status.CANCELLED


# --- GET cannot execute / mandatory reason / confirmation flow ---

def test_get_request_cannot_trigger_action(superuser_client, order):
    resp = superuser_client.get(CHANGELIST_URL, {
        "action": "cancel_order_action", "_selected_action": [str(order.pk)], "confirm_apply": "1", "reason": "via GET",
    })
    order.refresh_from_db()
    assert order.status == Order.Status.PENDING  # GET query params never reach response_action's POST handling


def test_missing_reason_shows_error_and_no_mutation(superuser_client, order):
    resp = superuser_client.post(CHANGELIST_URL, {
        "action": "cancel_order_action", "_selected_action": [str(order.pk)], "confirm_apply": "1", "reason": "",
    })
    assert resp.status_code == 200
    order.refresh_from_db()
    assert order.status == Order.Status.PENDING
    assert AdminAuditLog.objects.count() == 0


def test_first_post_without_confirm_renders_intermediate_page(superuser_client, order):
    resp = superuser_client.post(CHANGELIST_URL, {
        "action": "cancel_order_action", "_selected_action": [str(order.pk)],
    })
    assert resp.status_code == 200
    assert b"reason" in resp.content.lower()
    order.refresh_from_db()
    assert order.status == Order.Status.PENDING  # nothing happened yet


def test_confirmation_page_warns_about_verified_payments(superuser_client):
    order = make_order(email="order-admin-paid-warning@example.com")
    installment = order.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    attempt = PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentCreditService().apply_verified_success(attempt.id)

    resp = superuser_client.post(CHANGELIST_URL, {
        "action": "cancel_order_action", "_selected_action": [str(order.pk)],
    })
    assert resp.status_code == 200
    assert b"does not refund money" in resp.content


def test_csrf_enforced(order):
    User.objects.create_superuser(username="csrfadmin", email="c@example.com", password="x")
    client = Client(enforce_csrf_checks=True)
    client.login(username="csrfadmin", password="x")
    resp = client.post(CHANGELIST_URL, {
        "action": "cancel_order_action", "_selected_action": [str(order.pk)],
        "confirm_apply": "1", "reason": "no csrf token",
    })
    assert resp.status_code == 403
    order.refresh_from_db()
    assert order.status == Order.Status.PENDING


# --- Manual disposition ---

def test_apply_manual_disposition_requires_permission(order):
    client, _ = staff_client_with_perms()  # view only, no disposition permission
    resp = client.post(CHANGELIST_URL, {
        "action": "apply_manual_disposition_action", "_selected_action": [str(order.pk)],
        "confirm_apply": "1", "reason": "trying", "disposition": Order.ManualDisposition.NEEDS_REVIEW,
    })
    assert resp.status_code == 403
    order.refresh_from_db()
    assert order.manual_disposition == Order.ManualDisposition.NONE


def test_apply_manual_disposition_success(superuser_client, order):
    resp = superuser_client.post(CHANGELIST_URL, {
        "action": "apply_manual_disposition_action", "_selected_action": [str(order.pk)],
        "confirm_apply": "1", "reason": "flag for review", "disposition": Order.ManualDisposition.NEEDS_REVIEW,
    }, follow=True)
    assert resp.status_code == 200
    order.refresh_from_db()
    assert order.manual_disposition == Order.ManualDisposition.NEEDS_REVIEW
    assert order.status == Order.Status.PENDING  # provider/financial status untouched


# --- Read-only record fields / search / filter ---

def test_order_fields_remain_readonly(superuser_client, order):
    resp = superuser_client.post(
        reverse("admin:payments_order_change", args=[order.pk]),
        data={"status": Order.Status.COMPLETED},
    )
    assert resp.status_code == 403  # OrderAdmin.has_change_permission is False


def test_changelist_search_by_reference(superuser_client, order):
    resp = superuser_client.get(CHANGELIST_URL, {"q": str(order.reference)})
    assert resp.status_code == 200
    assert str(order.reference).encode() in resp.content


def test_changelist_filter_by_status(superuser_client, order):
    resp = superuser_client.get(CHANGELIST_URL, {"status__exact": "PENDING"})
    assert resp.status_code == 200


def test_no_secrets_exposed_on_changelist(superuser_client, order):
    resp = superuser_client.get(CHANGELIST_URL)
    body = resp.content.decode().lower()
    assert "api_key" not in body
    assert "webhook_secret" not in body
