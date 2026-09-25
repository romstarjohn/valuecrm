from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission, User
from django.test import Client
from django.urls import reverse

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import AdminAuditLog, Installment, PaymentAttempt, PaymentPlan
from apps.payments.services import OrderService, PaymentAttemptService, PaymentCreditService

pytestmark = pytest.mark.django_db

CHANGELIST_URL = reverse("admin:payments_installment_changelist")


def make_order(email, installment_count=1, installment_amount=Decimal("100000.00")):
    course = Course.objects.create(cf_course_id=f"crs_{email}", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code=f"plan_{email}", name="Bootcamp", course=course,
        installment_count=installment_count, installment_amount=installment_amount, is_active=True,
    )
    contact = Contact.objects.create(email=email)
    order, _ = OrderService().create_order(contact, plan.id, f"idem-{email}")
    return order


@pytest.fixture
def installment(db):
    order = make_order("installment-admin@example.com")
    return order.installments.get()


@pytest.fixture
def superuser_client(db):
    User.objects.create_superuser(username="installmentadmin", email="ia@example.com", password="x")
    client = Client()
    client.login(username="installmentadmin", password="x")
    return client


def test_cancel_requires_permission(installment):
    user = User.objects.create_user(username="staffinstallment", password="x", is_staff=True)
    user.user_permissions.add(Permission.objects.get(codename="view_installment"))
    client = Client()
    client.login(username="staffinstallment", password="x")

    resp = client.post(CHANGELIST_URL, {
        "action": "cancel_installment_action", "_selected_action": [str(installment.pk)],
        "confirm_apply": "1", "reason": "trying",
    })
    assert resp.status_code == 302
    installment.refresh_from_db()
    assert installment.status == Installment.Status.SCHEDULED


def test_cancel_via_admin(superuser_client, installment):
    resp = superuser_client.post(CHANGELIST_URL, {
        "action": "cancel_installment_action", "_selected_action": [str(installment.pk)],
        "confirm_apply": "1", "reason": "not needed anymore",
    }, follow=True)
    assert resp.status_code == 200
    installment.refresh_from_db()
    assert installment.status == Installment.Status.CANCELLED


def test_waive_requires_permission(installment):
    user = User.objects.create_user(username="staffwaive", password="x", is_staff=True)
    user.user_permissions.add(Permission.objects.get(codename="view_installment"))
    client = Client()
    client.login(username="staffwaive", password="x")

    resp = client.post(CHANGELIST_URL, {
        "action": "waive_installment_action", "_selected_action": [str(installment.pk)],
        "confirm_apply": "1", "reason": "trying",
    })
    assert resp.status_code == 302
    installment.refresh_from_db()
    assert installment.status == Installment.Status.SCHEDULED


def test_waive_via_admin(superuser_client, installment):
    resp = superuser_client.post(CHANGELIST_URL, {
        "action": "waive_installment_action", "_selected_action": [str(installment.pk)],
        "confirm_apply": "1", "reason": "goodwill waiver",
    }, follow=True)
    assert resp.status_code == 200
    installment.refresh_from_db()
    assert installment.status == Installment.Status.WAIVED
    assert not installment.paid_amount


def test_paid_installment_cannot_be_cancelled_via_admin(superuser_client):
    order = make_order("installment-admin-paid@example.com")
    installment = order.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    attempt = PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentCreditService().apply_verified_success(attempt.id)
    installment.refresh_from_db()

    resp = superuser_client.post(CHANGELIST_URL, {
        "action": "cancel_installment_action", "_selected_action": [str(installment.pk)],
        "confirm_apply": "1", "reason": "should fail",
    }, follow=True)
    assert resp.status_code == 200
    installment.refresh_from_db()
    assert installment.status == Installment.Status.PAID


def test_readonly_fields(superuser_client, installment):
    resp = superuser_client.post(
        reverse("admin:payments_installment_change", args=[installment.pk]),
        data={"status": Installment.Status.PAID},
    )
    assert resp.status_code == 403
