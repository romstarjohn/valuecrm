from decimal import Decimal
from unittest.mock import Mock

import pytest
from django.contrib.auth.models import Permission, User
from django.test import Client
from django.urls import reverse

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import AdminAuditLog, PaymentAttempt, PaymentPlan
from apps.payments.services import OrderService, PaymentAttemptService
from integrations.payments.tara.schemas import TaraTransactionStatusResponse

pytestmark = pytest.mark.django_db

CHANGELIST_URL = reverse("admin:payments_paymentattempt_changelist")


@pytest.fixture
def pending_attempt(db):
    course = Course.objects.create(cf_course_id="crs_check_status", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code="check-status-plan", name="Bootcamp", course=course,
        installment_count=1, installment_amount=Decimal("100000.00"), is_active=True,
    )
    contact = Contact.objects.create(email="check-status-admin@example.com")
    order, _ = OrderService().create_order(contact, plan.id, "idem-check-status-admin")
    installment = order.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    attempt = PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    attempt = PaymentAttemptService().transition(attempt, PaymentAttempt.Status.PENDING)
    return attempt


@pytest.fixture
def superuser_client(db):
    User.objects.create_superuser(username="attemptadmin", email="aa@example.com", password="x")
    client = Client()
    client.login(username="attemptadmin", password="x")
    return client


def test_staff_without_permission_gets_403(pending_attempt):
    user = User.objects.create_user(username="staffnoop", password="x", is_staff=True)
    user.user_permissions.add(Permission.objects.get(codename="view_paymentattempt"))
    client = Client()
    client.login(username="staffnoop", password="x")

    resp = client.post(CHANGELIST_URL, {
        "action": "check_tara_status_action", "_selected_action": [str(pending_attempt.pk)],
        "confirm_apply": "1", "reason": "trying",
    })
    assert resp.status_code == 302
    pending_attempt.refresh_from_db()
    assert pending_attempt.status == PaymentAttempt.Status.PENDING
    assert AdminAuditLog.objects.count() == 0


def test_check_status_success_via_admin(superuser_client, pending_attempt, mocker):
    mock_client = Mock()
    mock_client.check_transaction_status.return_value = TaraTransactionStatusResponse.model_validate({
        "productId": pending_attempt.tara_product_id, "status": "SUCCESS", "message": "ok",
    })
    mocker.patch("apps.payments.admin_services.TaraConfigService.get_client", return_value=mock_client)

    resp = superuser_client.post(CHANGELIST_URL, {
        "action": "check_tara_status_action", "_selected_action": [str(pending_attempt.pk)],
        "confirm_apply": "1", "reason": "checking stuck payment",
    }, follow=True)
    assert resp.status_code == 200

    pending_attempt.refresh_from_db()
    assert pending_attempt.status == PaymentAttempt.Status.SUCCEEDED
    log = AdminAuditLog.objects.get(payment_attempt=pending_attempt)
    assert log.reason == "checking stuck payment"


def test_check_status_never_shows_provider_error_body(superuser_client, pending_attempt, mocker):
    mock_client = Mock()
    mock_client.check_transaction_status.side_effect = Exception("raw provider body with secret=abc123")
    mocker.patch("apps.payments.admin_services.TaraConfigService.get_client", return_value=mock_client)

    # Exception isn't one of the handled Tara exceptions -> propagates as a generic error message
    # via the confirmed_admin_action wrapper, but must never leak into the audit log.
    resp = superuser_client.post(CHANGELIST_URL, {
        "action": "check_tara_status_action", "_selected_action": [str(pending_attempt.pk)],
        "confirm_apply": "1", "reason": "checking",
    }, follow=True)
    assert resp.status_code == 200
    assert b"secret=abc123" not in resp.content


def test_readonly_fields_cannot_be_hand_edited(superuser_client, pending_attempt):
    resp = superuser_client.post(
        reverse("admin:payments_paymentattempt_change", args=[pending_attempt.pk]),
        data={"status": PaymentAttempt.Status.SUCCEEDED},
    )
    assert resp.status_code == 403


def test_list_display_shows_provider_and_internal_fields_separately(superuser_client, pending_attempt):
    resp = superuser_client.get(CHANGELIST_URL)
    assert resp.status_code == 200
    assert pending_attempt.tara_product_id.encode() in resp.content
