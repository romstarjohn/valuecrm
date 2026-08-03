from decimal import Decimal
from unittest.mock import Mock

import pytest
from django.contrib.auth.models import Permission, User
from django.test import Client
from django.urls import reverse

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import AdminAuditLog, Installment, Order, PaymentAttempt, PaymentConfirmation, PaymentPlan
from apps.payments.services import OrderService, PaymentAttemptService, PaymentCreditService
from apps.provisioning.models import ProvisioningRequest
from integrations.payments.tara.schemas import TaraTransactionStatusResponse

pytestmark = pytest.mark.django_db


def make_order(email, installment_count=1, installment_amount=Decimal("100000.00")):
    course = Course.objects.create(cf_course_id=f"crs_{email}", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code=f"plan_{email}", name="Bootcamp", course=course,
        installment_count=installment_count, installment_amount=installment_amount, is_active=True,
    )
    contact = Contact.objects.create(email=email)
    order, _ = OrderService().create_order(contact, plan.id, f"idem-{email}")
    return order


def make_full_order(email):
    order = make_order(email)
    installment = order.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    attempt = PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentCreditService().apply_verified_success(attempt.id)
    return order


# --- Cancel order ---

def test_cancel_order_requires_permission():
    order = make_order("action-cancel-noperm@example.com")
    user = User.objects.create_user(username="cancelnoperm", password="x", is_staff=True)
    client = Client()
    client.login(username="cancelnoperm", password="x")
    url = reverse("operations:order_cancel", args=[order.reference])

    get_response = client.get(url)
    assert get_response.status_code == 403

    post_response = client.post(url, {"confirm_apply": "1", "reason": "trying anyway"})
    assert post_response.status_code == 403
    order.refresh_from_db()
    assert order.status == Order.Status.PENDING
    assert AdminAuditLog.objects.count() == 0


def test_cancel_order_get_shows_confirmation_page_no_mutation(ops_client):
    order = make_order("action-cancel-confirm-page@example.com")
    response = ops_client.get(reverse("operations:order_cancel", args=[order.reference]))
    assert response.status_code == 200
    assert b"reason" in response.content.lower() or b"Reason" in response.content
    order.refresh_from_db()
    assert order.status == Order.Status.PENDING


def test_cancel_order_requires_reason(ops_client):
    order = make_order("action-cancel-noreason@example.com")
    response = ops_client.post(
        reverse("operations:order_cancel", args=[order.reference]), {"confirm_apply": "1", "reason": ""},
    )
    assert response.status_code == 200
    order.refresh_from_db()
    assert order.status == Order.Status.PENDING
    assert AdminAuditLog.objects.count() == 0


def test_cancel_order_success_creates_audit_record(ops_client):
    order = make_order("action-cancel-success@example.com")
    response = ops_client.post(
        reverse("operations:order_cancel", args=[order.reference]),
        {"confirm_apply": "1", "reason": "customer requested cancellation"}, follow=True,
    )
    assert response.status_code == 200
    order.refresh_from_db()
    assert order.status == Order.Status.CANCELLED
    log = AdminAuditLog.objects.get(order=order, action_type=AdminAuditLog.ActionType.CANCEL_ORDER)
    assert log.reason == "customer requested cancellation"
    assert log.outcome_category == AdminAuditLog.OutcomeCategory.SUCCESS


def test_cancel_order_csrf_enforced(ops_client):
    order = make_order("action-cancel-csrf@example.com")
    User.objects.create_superuser(username="csrfsuper", email="c@example.com", password="x")
    strict_client = Client(enforce_csrf_checks=True)
    strict_client.login(username="csrfsuper", password="x")
    response = strict_client.post(
        reverse("operations:order_cancel", args=[order.reference]), {"confirm_apply": "1", "reason": "no csrf"},
    )
    assert response.status_code == 403
    order.refresh_from_db()
    assert order.status == Order.Status.PENDING


def test_cancel_order_reload_ignores_hidden_state(ops_client):
    """Target is always reloaded server-side by reference from the URL — no order state is ever trusted from POST body."""
    order = make_order("action-cancel-server-reload@example.com")
    response = ops_client.post(
        reverse("operations:order_cancel", args=[order.reference]),
        {"confirm_apply": "1", "reason": "server reload check", "status": "COMPLETED", "reference": "00000000-0000-0000-0000-000000000000"},
        follow=True,
    )
    order.refresh_from_db()
    assert order.status == Order.Status.CANCELLED  # driven only by the URL reference, not POST body fields


# --- Manual disposition ---

def test_apply_disposition_requires_permission():
    order = make_order("action-disposition-noperm@example.com")
    user = User.objects.create_user(username="dispositionnoperm", password="x", is_staff=True)
    client = Client()
    client.login(username="dispositionnoperm", password="x")
    response = client.post(
        reverse("operations:order_disposition", args=[order.reference]) + "?value=NEEDS_REVIEW",
        {"confirm_apply": "1", "reason": "trying"},
    )
    assert response.status_code == 403
    order.refresh_from_db()
    assert order.manual_disposition == Order.ManualDisposition.NONE


def test_apply_disposition_success(ops_client):
    order = make_order("action-disposition-success@example.com")
    response = ops_client.post(
        reverse("operations:order_disposition", args=[order.reference]) + "?value=DISPUTED",
        {"confirm_apply": "1", "reason": "flagging as disputed"}, follow=True,
    )
    assert response.status_code == 200
    order.refresh_from_db()
    assert order.manual_disposition == Order.ManualDisposition.DISPUTED
    assert order.status == Order.Status.PENDING  # never touches provider/financial status


# --- Cancel / waive installment ---

def test_cancel_installment_requires_permission():
    order = make_order("action-installment-cancel-noperm@example.com")
    installment = order.installments.get()
    user = User.objects.create_user(username="installmentcancelnoperm", password="x", is_staff=True)
    client = Client()
    client.login(username="installmentcancelnoperm", password="x")
    response = client.post(
        reverse("operations:installment_cancel", args=[installment.pk]), {"confirm_apply": "1", "reason": "trying"},
    )
    assert response.status_code == 403
    installment.refresh_from_db()
    assert installment.status == Installment.Status.SCHEDULED


def test_cancel_installment_success(ops_client):
    order = make_order("action-installment-cancel-success@example.com")
    installment = order.installments.get()
    response = ops_client.post(
        reverse("operations:installment_cancel", args=[installment.pk]),
        {"confirm_apply": "1", "reason": "not needed"}, follow=True,
    )
    assert response.status_code == 200
    installment.refresh_from_db()
    assert installment.status == Installment.Status.CANCELLED


def test_waive_installment_success(ops_client):
    order = make_order("action-installment-waive-success@example.com")
    installment = order.installments.get()
    response = ops_client.post(
        reverse("operations:installment_waive", args=[installment.pk]),
        {"confirm_apply": "1", "reason": "goodwill waiver"}, follow=True,
    )
    assert response.status_code == 200
    installment.refresh_from_db()
    assert installment.status == Installment.Status.WAIVED
    assert not installment.paid_amount


def test_waive_paid_installment_rejected_safely(ops_client):
    order = make_full_order("action-installment-waive-paid@example.com")
    installment = order.installments.get()
    response = ops_client.post(
        reverse("operations:installment_waive", args=[installment.pk]),
        {"confirm_apply": "1", "reason": "should fail"}, follow=True,
    )
    assert response.status_code == 200
    installment.refresh_from_db()
    assert installment.status == Installment.Status.PAID  # unchanged


# --- Check Tara status ---

def test_check_tara_status_requires_permission():
    order = make_order("action-check-status-noperm@example.com")
    installment = order.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    attempt = PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    attempt = PaymentAttemptService().transition(attempt, PaymentAttempt.Status.PENDING)
    user = User.objects.create_user(username="checkstatusnoperm", password="x", is_staff=True)
    client = Client()
    client.login(username="checkstatusnoperm", password="x")
    response = client.post(
        reverse("operations:payment_attempt_check_status", args=[attempt.pk]), {"confirm_apply": "1", "reason": "trying"},
    )
    assert response.status_code == 403
    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.PENDING


def test_check_tara_status_no_external_call_for_unauthorized(mocker):
    order = make_order("action-check-status-no-call@example.com")
    installment = order.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    attempt = PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    attempt = PaymentAttemptService().transition(attempt, PaymentAttempt.Status.PENDING)

    mock_get_client = mocker.patch("apps.payments.admin_services.TaraConfigService.get_client")
    user = User.objects.create_user(username="checkstatusnocall", password="x", is_staff=True)
    client = Client()
    client.login(username="checkstatusnocall", password="x")
    client.post(reverse("operations:payment_attempt_check_status", args=[attempt.pk]), {"confirm_apply": "1", "reason": "trying"})

    mock_get_client.assert_not_called()


def test_check_tara_status_success(ops_client, mocker):
    order = make_order("action-check-status-success@example.com")
    installment = order.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    attempt = PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    attempt = PaymentAttemptService().transition(attempt, PaymentAttempt.Status.PENDING)

    mock_client = Mock()
    mock_client.check_transaction_status.return_value = TaraTransactionStatusResponse.model_validate({
        "productId": attempt.tara_product_id, "status": "SUCCESS", "message": "ok",
    })
    mocker.patch("apps.payments.admin_services.TaraConfigService.get_client", return_value=mock_client)

    response = ops_client.post(
        reverse("operations:payment_attempt_check_status", args=[attempt.pk]),
        {"confirm_apply": "1", "reason": "checking stuck payment"}, follow=True,
    )
    assert response.status_code == 200
    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.SUCCEEDED
    log = AdminAuditLog.objects.get(payment_attempt=attempt)
    assert log.reason == "checking stuck payment"


# --- Retry confirmation ---

def test_retry_confirmation_requires_permission():
    order = make_full_order("action-retry-confirm-noperm@example.com")
    confirmation = PaymentConfirmation.objects.get(order=order)
    confirmation.status = PaymentConfirmation.Status.FAILED
    confirmation.save()
    user = User.objects.create_user(username="retryconfirmnoperm", password="x", is_staff=True)
    client = Client()
    client.login(username="retryconfirmnoperm", password="x")
    response = client.post(
        reverse("operations:confirmation_retry", args=[confirmation.pk]), {"confirm_apply": "1", "reason": "trying"},
    )
    assert response.status_code == 403
    confirmation.refresh_from_db()
    assert confirmation.status == PaymentConfirmation.Status.FAILED


def test_retry_confirmation_success_no_email_sent(ops_client):
    from django.core import mail
    order = make_full_order("action-retry-confirm-success@example.com")
    confirmation = PaymentConfirmation.objects.get(order=order)
    confirmation.status = PaymentConfirmation.Status.FAILED
    confirmation.save()
    mail.outbox.clear()

    response = ops_client.post(
        reverse("operations:confirmation_retry", args=[confirmation.pk]),
        {"confirm_apply": "1", "reason": "retry via dashboard"}, follow=True,
    )
    assert response.status_code == 200
    confirmation.refresh_from_db()
    assert confirmation.status == PaymentConfirmation.Status.PENDING
    assert len(mail.outbox) == 0


# --- Retry provisioning ---

def test_retry_provisioning_requires_permission():
    order = make_full_order("action-retry-provisioning-noperm@example.com")
    req = ProvisioningRequest.objects.get(order=order)
    req.status = ProvisioningRequest.Status.FAILED
    req.save()
    user = User.objects.create_user(username="retryprovisioningnoperm", password="x", is_staff=True)
    client = Client()
    client.login(username="retryprovisioningnoperm", password="x")
    response = client.post(
        reverse("operations:provisioning_retry", args=[req.pk]), {"confirm_apply": "1", "reason": "trying"},
    )
    assert response.status_code == 403
    req.refresh_from_db()
    assert req.status == ProvisioningRequest.Status.FAILED


def test_retry_provisioning_success_no_clickfunnels_call(ops_client, mocker):
    order = make_full_order("action-retry-provisioning-success@example.com")
    req = ProvisioningRequest.objects.get(order=order)
    req.status = ProvisioningRequest.Status.FAILED
    req.save()
    mock_execute = mocker.patch("apps.provisioning.services.ProvisioningService.execute")

    response = ops_client.post(
        reverse("operations:provisioning_retry", args=[req.pk]),
        {"confirm_apply": "1", "reason": "operator fixed it"}, follow=True,
    )
    assert response.status_code == 200
    req.refresh_from_db()
    assert req.status == ProvisioningRequest.Status.PENDING
    mock_execute.assert_not_called()
    log = AdminAuditLog.objects.get(action_type=AdminAuditLog.ActionType.RETRY_PROVISIONING)
    assert log.reason == "operator fixed it"
