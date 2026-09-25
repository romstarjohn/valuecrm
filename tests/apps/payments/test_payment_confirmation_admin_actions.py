from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission, User
from django.core import mail
from django.test import Client
from django.urls import reverse

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import AdminAuditLog, PaymentAttempt, PaymentConfirmation, PaymentPlan
from apps.payments.services import OrderService, PaymentAttemptService, PaymentCreditService

pytestmark = pytest.mark.django_db

CHANGELIST_URL = reverse("admin:payments_paymentconfirmation_changelist")


@pytest.fixture
def failed_confirmation(db):
    course = Course.objects.create(cf_course_id="crs_confirm_admin", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code="confirm-admin-plan", name="Bootcamp", course=course,
        installment_count=1, installment_amount=Decimal("100000.00"), is_active=True,
    )
    contact = Contact.objects.create(email="confirm-admin@example.com")
    order, _ = OrderService().create_order(contact, plan.id, "idem-confirm-admin")
    installment = order.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    attempt = PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentCreditService().apply_verified_success(attempt.id)
    confirmation = PaymentConfirmation.objects.get(payment_attempt=attempt)
    confirmation.status = PaymentConfirmation.Status.FAILED
    confirmation.save()
    return confirmation


@pytest.fixture
def superuser_client(db):
    User.objects.create_superuser(username="confirmadmin", email="ca@example.com", password="x")
    client = Client()
    client.login(username="confirmadmin", password="x")
    return client


def test_staff_without_permission_gets_403(failed_confirmation):
    user = User.objects.create_user(username="staffconfirm", password="x", is_staff=True)
    user.user_permissions.add(Permission.objects.get(codename="view_paymentconfirmation"))
    client = Client()
    client.login(username="staffconfirm", password="x")

    resp = client.post(CHANGELIST_URL, {
        "action": "retry_confirmation_action", "_selected_action": [str(failed_confirmation.pk)],
        "confirm_apply": "1", "reason": "trying",
    })
    assert resp.status_code == 302
    failed_confirmation.refresh_from_db()
    assert failed_confirmation.status == PaymentConfirmation.Status.FAILED


def test_retry_via_admin_does_not_send_email(superuser_client, failed_confirmation):
    mail.outbox.clear()
    resp = superuser_client.post(CHANGELIST_URL, {
        "action": "retry_confirmation_action", "_selected_action": [str(failed_confirmation.pk)],
        "confirm_apply": "1", "reason": "retry via admin",
    }, follow=True)
    assert resp.status_code == 200

    failed_confirmation.refresh_from_db()
    assert failed_confirmation.status == PaymentConfirmation.Status.PENDING
    assert len(mail.outbox) == 0
    log = AdminAuditLog.objects.get(action_type=AdminAuditLog.ActionType.RETRY_CONFIRMATION)
    assert log.reason == "retry via admin"


def test_sent_confirmation_cannot_be_retried_via_admin(superuser_client, failed_confirmation):
    failed_confirmation.status = PaymentConfirmation.Status.SENT
    failed_confirmation.save()

    resp = superuser_client.post(CHANGELIST_URL, {
        "action": "retry_confirmation_action", "_selected_action": [str(failed_confirmation.pk)],
        "confirm_apply": "1", "reason": "should be rejected",
    }, follow=True)
    assert resp.status_code == 200
    failed_confirmation.refresh_from_db()
    assert failed_confirmation.status == PaymentConfirmation.Status.SENT


def test_readonly_fields(superuser_client, failed_confirmation):
    resp = superuser_client.post(
        reverse("admin:payments_paymentconfirmation_change", args=[failed_confirmation.pk]),
        data={"status": PaymentConfirmation.Status.SENT},
    )
    assert resp.status_code == 403
