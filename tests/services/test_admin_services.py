"""
Phase 8 (docs/TARA_INTEGRATION_PROJECT.md): service-level coverage for
apps/payments/admin_services.py — the business logic behind every
administrative admin action. Admin-layer tests (permission/CSRF/confirmation
flow/read-only enforcement) live in tests/apps/payments and
tests/apps/provisioning instead of being duplicated here.
"""
from decimal import Decimal
from unittest.mock import Mock

import pytest
from django.contrib.auth.models import User

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.admin_services import (
    AdminActionError,
    OrderAdministrationService,
    PaymentAttemptAdministrationService,
    PaymentConfirmationAdministrationService,
)
from apps.payments.models import AdminAuditLog, Installment, Order, PaymentAttempt, PaymentConfirmation, PaymentPlan
from apps.payments.services import (
    OrderService,
    PaymentAttemptService,
    PaymentConfirmationService,
    PaymentCreditService,
)
from integrations.payments.tara.exceptions import (
    TaraConfigurationError,
    TaraConnectionError,
    TaraMalformedResponseError,
)
from integrations.payments.tara.schemas import TaraTransactionStatusResponse

pytestmark = pytest.mark.django_db


def make_order(installment_count=1, installment_amount=Decimal("100000.00"), email="buyer@example.com", access_policy=None):
    course = Course.objects.create(cf_course_id=f"crs_{email}", name="Bootcamp", workspace_id="ws_1")
    kwargs = dict(
        code=f"plan_{email}", name="Bootcamp", course=course,
        installment_count=installment_count, installment_amount=installment_amount, is_active=True,
    )
    if access_policy is not None:
        kwargs["access_policy"] = access_policy
    plan = PaymentPlan.objects.create(**kwargs)
    contact = Contact.objects.create(email=email)
    order, _ = OrderService().create_order(contact, plan.id, f"idem-{email}")
    return order


def create_payable_attempt(installment: Installment) -> PaymentAttempt:
    attempt = PaymentAttemptService().create_attempt(installment)
    return PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)


@pytest.fixture
def admin_user(db):
    return User.objects.create_superuser(username="ops", email="ops@example.com", password="x")


# --- OrderAdministrationService.cancel_order ---

def test_cancel_order_success():
    order = make_order(email="cancel-success@example.com")
    admin = User.objects.create_superuser(username="a1", email="a1@x.com", password="x")

    updated = OrderAdministrationService().cancel_order(order.id, "customer requested", admin)

    assert updated.status == Order.Status.CANCELLED
    log = AdminAuditLog.objects.get(order=order, action_type=AdminAuditLog.ActionType.CANCEL_ORDER)
    assert log.outcome_category == AdminAuditLog.OutcomeCategory.SUCCESS
    assert log.reason == "customer requested"
    assert log.administrator_username == "a1"


def test_cancel_order_idempotent_when_already_cancelled(admin_user):
    order = make_order(email="cancel-idempotent@example.com")
    service = OrderAdministrationService()
    service.cancel_order(order.id, "first cancel", admin_user)

    updated = service.cancel_order(order.id, "second cancel attempt", admin_user)

    assert updated.status == Order.Status.CANCELLED
    logs = AdminAuditLog.objects.filter(order=order, action_type=AdminAuditLog.ActionType.CANCEL_ORDER)
    assert logs.count() == 2
    assert logs.first().outcome_category == AdminAuditLog.OutcomeCategory.NO_OP_ALREADY_IN_STATE


def test_cancel_order_rejects_completed_order(admin_user):
    order = make_order(email="cancel-completed@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)
    PaymentCreditService().apply_verified_success(attempt.id)
    order.refresh_from_db()
    assert order.status == Order.Status.COMPLETED

    with pytest.raises(AdminActionError):
        OrderAdministrationService().cancel_order(order.id, "should fail", admin_user)

    order.refresh_from_db()
    assert order.status == Order.Status.COMPLETED  # untouched
    log = AdminAuditLog.objects.get(order=order, action_type=AdminAuditLog.ActionType.CANCEL_ORDER)
    assert log.outcome_category == AdminAuditLog.OutcomeCategory.REJECTED_INVALID_STATE


def test_cancel_order_preserves_paid_installment_and_succeeded_attempt(admin_user):
    order = make_order(installment_count=2, installment_amount=Decimal("50000.00"), email="cancel-preserve@example.com")
    installments = list(order.installments.order_by("sequence"))
    attempt = create_payable_attempt(installments[0])
    PaymentCreditService().apply_verified_success(attempt.id)
    order.refresh_from_db()
    assert order.status == Order.Status.ACTIVE

    OrderAdministrationService().cancel_order(order.id, "cancel remaining", admin_user)

    installments[0].refresh_from_db()
    attempt.refresh_from_db()
    installments[1].refresh_from_db()
    assert installments[0].status == Installment.Status.PAID  # untouched
    assert attempt.status == PaymentAttempt.Status.SUCCEEDED  # untouched
    assert installments[1].status == Installment.Status.CANCELLED  # the unpaid one


def test_cancel_order_expires_active_unpaid_attempts(admin_user):
    order = make_order(email="cancel-expires-attempt@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)
    assert attempt.status == PaymentAttempt.Status.LINK_CREATED

    OrderAdministrationService().cancel_order(order.id, "cancel with pending attempt", admin_user)

    attempt.refresh_from_db()
    installment.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.EXPIRED
    assert installment.status == Installment.Status.CANCELLED


def test_cancel_order_no_refund_no_tara_no_clickfunnels_calls(admin_user, mocker):
    tara_mock = mocker.patch("apps.payments.admin_services.TaraConfigService")
    order = make_order(email="cancel-no-external@example.com")
    OrderAdministrationService().cancel_order(order.id, "no external calls", admin_user)
    tara_mock.assert_not_called()


# --- OrderAdministrationService.cancel_installment ---

def test_cancel_installment_success(admin_user):
    order = make_order(email="cancel-installment@example.com")
    installment = order.installments.get()

    updated = OrderAdministrationService().cancel_installment(installment.id, "not needed", admin_user)

    assert updated.status == Installment.Status.CANCELLED


def test_cancel_installment_rejects_paid(admin_user):
    order = make_order(email="cancel-installment-paid@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)
    PaymentCreditService().apply_verified_success(attempt.id)
    installment.refresh_from_db()

    with pytest.raises(AdminActionError):
        OrderAdministrationService().cancel_installment(installment.id, "should fail", admin_user)

    installment.refresh_from_db()
    assert installment.status == Installment.Status.PAID


# --- OrderAdministrationService.waive_installment ---

def test_waive_installment_success_keeps_paid_amount_zero(admin_user):
    order = make_order(installment_count=2, installment_amount=Decimal("50000.00"), email="waive-basic@example.com")
    installment = order.installments.order_by("sequence").first()

    updated = OrderAdministrationService().waive_installment(installment.id, "goodwill waiver", admin_user)

    assert updated.status == Installment.Status.WAIVED
    assert not updated.paid_amount


def test_waive_installment_rejects_paid(admin_user):
    order = make_order(email="waive-paid@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)
    PaymentCreditService().apply_verified_success(attempt.id)
    installment.refresh_from_db()

    with pytest.raises(AdminActionError):
        OrderAdministrationService().waive_installment(installment.id, "should fail", admin_user)


def test_waive_installment_creates_no_confirmation():
    order = make_order(email="waive-no-confirmation@example.com")
    installment = order.installments.get()
    admin = User.objects.create_superuser(username="wnc", email="wnc@x.com", password="x")

    OrderAdministrationService().waive_installment(installment.id, "waived, no email", admin)

    assert PaymentConfirmation.objects.filter(installment=installment).count() == 0


def test_waive_installment_recomputes_order_completion(admin_user):
    order = make_order(installment_count=1, installment_amount=Decimal("100000.00"), email="waive-completes@example.com")
    installment = order.installments.get()
    assert order.status == Order.Status.PENDING

    OrderAdministrationService().waive_installment(installment.id, "full waiver", admin_user)

    order.refresh_from_db()
    assert order.status == Order.Status.COMPLETED


def test_waive_installment_newly_eligible_full_payment_order_creates_one_provisioning_request(admin_user):
    order = make_order(
        installment_count=2, installment_amount=Decimal("50000.00"), email="waive-eligible@example.com",
        access_policy=PaymentPlan.AccessPolicy.FULL_PAYMENT,
    )
    installments = list(order.installments.order_by("sequence"))
    attempt = create_payable_attempt(installments[0])
    PaymentCreditService().apply_verified_success(attempt.id)
    order.refresh_from_db()
    assert order.is_access_eligible is False

    OrderAdministrationService().waive_installment(installments[1].id, "waive final installment", admin_user)

    from apps.provisioning.models import ProvisioningRequest
    order.refresh_from_db()
    assert order.is_access_eligible is True
    assert ProvisioningRequest.objects.filter(order=order).count() == 1


def test_duplicate_waiver_is_safe(admin_user):
    order = make_order(email="waive-duplicate@example.com")
    installment = order.installments.get()
    service = OrderAdministrationService()
    service.waive_installment(installment.id, "first waiver", admin_user)

    updated = service.waive_installment(installment.id, "second waiver attempt", admin_user)

    assert updated.status == Installment.Status.WAIVED
    logs = AdminAuditLog.objects.filter(installment=installment, action_type=AdminAuditLog.ActionType.WAIVE_INSTALLMENT)
    assert logs.first().outcome_category == AdminAuditLog.OutcomeCategory.NO_OP_ALREADY_IN_STATE


# --- OrderAdministrationService.apply_manual_disposition ---

def test_apply_manual_disposition_success(admin_user):
    order = make_order(email="disposition-success@example.com")

    updated = OrderAdministrationService().apply_manual_disposition(
        order.id, Order.ManualDisposition.NEEDS_REVIEW, "flagging for review", admin_user,
    )

    assert updated.manual_disposition == Order.ManualDisposition.NEEDS_REVIEW
    assert updated.status == Order.Status.PENDING  # provider/financial status untouched


def test_apply_manual_disposition_rejects_invalid_value(admin_user):
    order = make_order(email="disposition-invalid@example.com")
    with pytest.raises(AdminActionError):
        OrderAdministrationService().apply_manual_disposition(order.id, "REFUNDED", "not a real option", admin_user)


def test_apply_manual_disposition_requires_reason_enforced_by_caller():
    """The service itself requires a non-empty `reason` string type; empty-reason rejection is enforced at the admin-action layer (see admin_actions.py)."""
    order = make_order(email="disposition-reason@example.com")
    admin = User.objects.create_superuser(username="dr", email="dr@x.com", password="x")
    log_count_before = AdminAuditLog.objects.count()
    OrderAdministrationService().apply_manual_disposition(order.id, Order.ManualDisposition.DISPUTED, "explicit reason", admin)
    assert AdminAuditLog.objects.count() == log_count_before + 1


# --- PaymentAttemptAdministrationService.check_tara_status ---

def test_check_tara_status_success_credits_payment(admin_user, mocker):
    order = make_order(email="check-status-success@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.PENDING)

    mock_client = Mock()
    mock_client.check_transaction_status.return_value = TaraTransactionStatusResponse.model_validate({
        "productId": attempt.tara_product_id, "status": "SUCCESS", "message": "ok",
    })
    mocker.patch("apps.payments.admin_services.TaraConfigService.get_client", return_value=mock_client)

    updated = PaymentAttemptAdministrationService().check_tara_status(attempt.id, "checking stuck payment", admin_user)

    assert updated.status == PaymentAttempt.Status.SUCCEEDED
    installment.refresh_from_db()
    assert installment.status == Installment.Status.PAID
    log = AdminAuditLog.objects.get(payment_attempt=attempt)
    assert log.outcome_category == AdminAuditLog.OutcomeCategory.SUCCESS
    assert log.order_id == order.id


def test_check_tara_status_failure_applies_failed_state(admin_user, mocker):
    order = make_order(email="check-status-failure@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.PENDING)

    mock_client = Mock()
    mock_client.check_transaction_status.return_value = TaraTransactionStatusResponse.model_validate({
        "productId": attempt.tara_product_id, "status": "FAILURE", "message": "declined",
    })
    mocker.patch("apps.payments.admin_services.TaraConfigService.get_client", return_value=mock_client)

    updated = PaymentAttemptAdministrationService().check_tara_status(attempt.id, "checking", admin_user)

    assert updated.status == PaymentAttempt.Status.FAILED


def test_check_tara_status_pending_stays_non_final(admin_user, mocker):
    order = make_order(email="check-status-pending@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.PENDING)

    mock_client = Mock()
    mock_client.check_transaction_status.return_value = TaraTransactionStatusResponse.model_validate({
        "productId": attempt.tara_product_id, "status": "PENDING", "message": "still processing",
    })
    mocker.patch("apps.payments.admin_services.TaraConfigService.get_client", return_value=mock_client)

    updated = PaymentAttemptAdministrationService().check_tara_status(attempt.id, "checking", admin_user)

    assert updated.status == PaymentAttempt.Status.PENDING
    log = AdminAuditLog.objects.get(payment_attempt=attempt)
    assert log.outcome_category == AdminAuditLog.OutcomeCategory.PROVIDER_NON_FINAL


def test_check_tara_status_unknown_stays_non_final(admin_user, mocker):
    order = make_order(email="check-status-unknown@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.UNKNOWN)

    mock_client = Mock()
    mock_client.check_transaction_status.return_value = TaraTransactionStatusResponse.model_validate({
        "productId": attempt.tara_product_id, "status": "SOMETHING_ELSE", "message": "",
    })
    mocker.patch("apps.payments.admin_services.TaraConfigService.get_client", return_value=mock_client)

    updated = PaymentAttemptAdministrationService().check_tara_status(attempt.id, "checking", admin_user)

    assert updated.status == PaymentAttempt.Status.UNKNOWN


@pytest.mark.parametrize("exc_class", [TaraConnectionError, TaraMalformedResponseError])
def test_check_tara_status_transient_errors_stay_non_final(admin_user, mocker, exc_class):
    order = make_order(email=f"check-status-{exc_class.__name__}@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.PENDING)

    mock_client = Mock()
    mock_client.check_transaction_status.side_effect = exc_class("boom")
    mocker.patch("apps.payments.admin_services.TaraConfigService.get_client", return_value=mock_client)

    updated = PaymentAttemptAdministrationService().check_tara_status(attempt.id, "checking", admin_user)

    assert updated.status == PaymentAttempt.Status.PENDING  # unchanged, non-final
    log = AdminAuditLog.objects.get(payment_attempt=attempt)
    assert log.outcome_category == AdminAuditLog.OutcomeCategory.PROVIDER_NON_FINAL
    assert "boom" not in log.reason
    assert "boom" not in log.previous_state
    assert "boom" not in log.resulting_state


def test_check_tara_status_product_id_mismatch_rejected(admin_user, mocker):
    order = make_order(email="check-status-mismatch@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.PENDING)

    mock_client = Mock()
    mock_client.check_transaction_status.return_value = TaraTransactionStatusResponse.model_validate({
        "productId": "not-the-right-id", "status": "SUCCESS", "message": "ok",
    })
    mocker.patch("apps.payments.admin_services.TaraConfigService.get_client", return_value=mock_client)

    updated = PaymentAttemptAdministrationService().check_tara_status(attempt.id, "checking", admin_user)

    assert updated.status == PaymentAttempt.Status.PENDING  # never credited from a mismatched response


def test_check_tara_status_no_configuration(admin_user, mocker):
    order = make_order(email="check-status-no-config@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.PENDING)
    mocker.patch("apps.payments.admin_services.TaraConfigService.get_client", side_effect=TaraConfigurationError("no config"))

    with pytest.raises(AdminActionError):
        PaymentAttemptAdministrationService().check_tara_status(attempt.id, "checking", admin_user)

    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.PENDING


def test_check_tara_status_rejects_non_pending_unknown_attempt(admin_user):
    order = make_order(email="check-status-wrong-state@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)  # LINK_CREATED, not PENDING/UNKNOWN

    with pytest.raises(AdminActionError):
        PaymentAttemptAdministrationService().check_tara_status(attempt.id, "checking", admin_user)


def test_check_tara_status_duplicate_success_remains_idempotent(admin_user, mocker):
    order = make_order(email="check-status-duplicate@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.PENDING)

    mock_client = Mock()
    mock_client.check_transaction_status.return_value = TaraTransactionStatusResponse.model_validate({
        "productId": attempt.tara_product_id, "status": "SUCCESS", "message": "ok",
    })
    mocker.patch("apps.payments.admin_services.TaraConfigService.get_client", return_value=mock_client)

    service = PaymentAttemptAdministrationService()
    service.check_tara_status(attempt.id, "first check", admin_user)
    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.SUCCEEDED

    # a duplicate check would normally be rejected pre-flight (status no longer PENDING/UNKNOWN)
    with pytest.raises(AdminActionError):
        service.check_tara_status(attempt.id, "second check", admin_user)

    installment.refresh_from_db()
    assert installment.paid_amount == installment.expected_amount  # not doubled


def test_check_tara_status_never_includes_credentials_in_audit(admin_user, mocker):
    order = make_order(email="check-status-no-secret-leak@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.PENDING)

    mock_client = Mock()
    mock_client.check_transaction_status.return_value = TaraTransactionStatusResponse.model_validate({
        "productId": attempt.tara_product_id, "status": "SUCCESS", "message": "ok",
    })
    mocker.patch("apps.payments.admin_services.TaraConfigService.get_client", return_value=mock_client)

    PaymentAttemptAdministrationService().check_tara_status(attempt.id, "checking", admin_user)

    log = AdminAuditLog.objects.get(payment_attempt=attempt)
    for field_value in (log.reason, log.previous_state, log.resulting_state, str(log.request_metadata)):
        assert "api_key" not in field_value.lower()
        assert "secret" not in field_value.lower()
        assert "bearer" not in field_value.lower()


# --- PaymentConfirmationAdministrationService.retry_confirmation ---

def make_confirmation(email="confirm-retry@example.com"):
    order = make_order(email=email)
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)
    PaymentCreditService().apply_verified_success(attempt.id)
    return PaymentConfirmation.objects.get(payment_attempt=attempt)


def test_retry_confirmation_from_failed(admin_user):
    confirmation = make_confirmation(email="retry-confirm-failed@example.com")
    confirmation.status = PaymentConfirmation.Status.FAILED
    confirmation.attempt_count = 3
    confirmation.last_failure_category = PaymentConfirmation.FailureCategory.TRANSIENT_DELIVERY_ERROR
    confirmation.save()

    updated = PaymentConfirmationAdministrationService().retry_confirmation(confirmation.id, "retry now", admin_user)

    assert updated.status == PaymentConfirmation.Status.PENDING
    assert updated.attempt_count == 0
    assert updated.last_failure_category == ""
    log = AdminAuditLog.objects.get(payment_attempt=confirmation.payment_attempt, action_type=AdminAuditLog.ActionType.RETRY_CONFIRMATION)
    assert "attempts=3" in log.previous_state
    assert "TRANSIENT_DELIVERY_ERROR" in log.previous_state


def test_retry_confirmation_from_manual_review(admin_user):
    confirmation = make_confirmation(email="retry-confirm-manual-review@example.com")
    confirmation.status = PaymentConfirmation.Status.MANUAL_REVIEW
    confirmation.save()

    updated = PaymentConfirmationAdministrationService().retry_confirmation(confirmation.id, "retry", admin_user)

    assert updated.status == PaymentConfirmation.Status.PENDING


def test_retry_confirmation_rejects_sent(admin_user):
    confirmation = make_confirmation(email="retry-confirm-sent@example.com")
    confirmation.status = PaymentConfirmation.Status.SENT
    confirmation.save()

    with pytest.raises(AdminActionError):
        PaymentConfirmationAdministrationService().retry_confirmation(confirmation.id, "should fail", admin_user)

    confirmation.refresh_from_db()
    assert confirmation.status == PaymentConfirmation.Status.SENT


def test_retry_confirmation_rejects_missing_recipient(admin_user):
    confirmation = make_confirmation(email="retry-confirm-no-recipient@example.com")
    confirmation.status = PaymentConfirmation.Status.FAILED
    confirmation.contact.email = ""
    confirmation.contact.save(update_fields=["email"])
    confirmation.save()

    with pytest.raises(AdminActionError):
        PaymentConfirmationAdministrationService().retry_confirmation(confirmation.id, "should fail", admin_user)


def test_retry_confirmation_does_not_send_email(admin_user):
    from django.core import mail
    confirmation = make_confirmation(email="retry-confirm-no-send@example.com")
    confirmation.status = PaymentConfirmation.Status.FAILED
    confirmation.save()
    mail.outbox.clear()

    PaymentConfirmationAdministrationService().retry_confirmation(confirmation.id, "retry", admin_user)

    assert len(mail.outbox) == 0


def test_duplicate_confirmation_retry_is_safely_rejected_not_corrupted(admin_user):
    """
    A second retry while the first is already PENDING (not FAILED/
    MANUAL_REVIEW) is rejected — safe in the sense of "no corruption, no
    duplicate side effect," not "silently succeeds twice."
    """
    confirmation = make_confirmation(email="retry-confirm-duplicate@example.com")
    confirmation.status = PaymentConfirmation.Status.FAILED
    confirmation.save()
    service = PaymentConfirmationAdministrationService()

    service.retry_confirmation(confirmation.id, "first retry", admin_user)
    with pytest.raises(AdminActionError):
        service.retry_confirmation(confirmation.id, "second retry", admin_user)

    confirmation.refresh_from_db()
    assert confirmation.status == PaymentConfirmation.Status.PENDING  # unchanged by the rejected second call
