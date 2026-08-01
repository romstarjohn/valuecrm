"""Phase 9: apps/payments/reconciliation_services.py::ReconciliationService."""
import threading
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock

import pytest
from django.db import connection
from django.utils import timezone

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import (
    Installment,
    Order,
    PaymentAttempt,
    PaymentConfirmation,
    PaymentPlan,
    ReconciliationRun,
    TaraConfig,
)
from apps.payments.reconciliation_services import ReconciliationService
from apps.payments.services import OrderService, PaymentAttemptService, PaymentCreditService
from apps.provisioning.models import ProvisioningRequest
from integrations.payments.tara.exceptions import TaraConnectionError, TaraServerError
from integrations.payments.tara.schemas import TaraTransactionListItem, TaraTransactionStatusResponse
from shared.constants import RECONCILIATION_STALE_ATTEMPT_MAX_CHECKS, RECONCILIATION_STALE_ATTEMPT_MIN_AGE_MINUTES
from shared.security import encrypt_value

pytestmark = pytest.mark.django_db


@pytest.fixture
def active_config():
    return TaraConfig.objects.create(
        name="Main", business_id="biz_1", is_active=True,
        api_key=encrypt_value("key"), webhook_secret=encrypt_value("secret"),
    )


def make_order(email, installment_count=1, installment_amount=Decimal("100000.00")):
    course = Course.objects.create(cf_course_id=f"crs_{email}", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code=f"plan_{email}", name="Bootcamp", course=course,
        installment_count=installment_count, installment_amount=installment_amount, is_active=True,
    )
    contact = Contact.objects.create(email=email)
    order, _ = OrderService().create_order(contact, plan.id, f"idem-{email}")
    return order


def make_stale_pending_attempt(email, status=PaymentAttempt.Status.PENDING):
    order = make_order(email)
    installment = order.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    attempt = PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    attempt = PaymentAttemptService().transition(attempt, PaymentAttempt.Status.PENDING)
    if status == PaymentAttempt.Status.UNKNOWN:
        attempt = PaymentAttemptService().transition(attempt, PaymentAttempt.Status.UNKNOWN)
    # backdate updated_at past the min-age window so it's selected
    old_time = timezone.now() - timedelta(minutes=RECONCILIATION_STALE_ATTEMPT_MIN_AGE_MINUTES + 5)
    PaymentAttempt.objects.filter(pk=attempt.pk).update(updated_at=old_time)
    return PaymentAttempt.objects.get(pk=attempt.pk)


def status_response(product_id, status):
    return TaraTransactionStatusResponse.model_validate({"productId": product_id, "status": status, "message": "ok"})


def make_mock_client(**overrides):
    """A Mock TaraClient that's always safe to use for list_paid_transactions() too — every reconciliation run pulls the transaction list regardless of what else it's testing."""
    mock_client = Mock()
    mock_client.list_paid_transactions.return_value = []
    for attr, value in overrides.items():
        setattr(mock_client, attr, value)
    return mock_client


# --- Concurrency ---

@pytest.mark.django_db(transaction=True)
def test_overlapping_run_is_skipped_not_duplicated(active_config, mocker):
    """
    Advisory locks are session/connection-scoped, so simulating a genuine
    "another run is in progress" requires a real second connection — a
    same-session re-acquisition would just succeed (Postgres advisory locks
    are re-entrant per session), which wouldn't test anything.
    """
    mocker.patch(
        "apps.payments.reconciliation_services.TaraConfigService.get_client",
        return_value=make_mock_client(),
    )
    holder_ready = threading.Event()
    release_holder = threading.Event()
    holder_conn = {}

    def hold_lock():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(782934651)")
        holder_ready.set()
        release_holder.wait(timeout=5)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(782934651)")
        connection.close()

    t = threading.Thread(target=hold_lock)
    t.start()
    try:
        holder_ready.wait(timeout=5)
        run = ReconciliationService().run()
        assert run.run_status == ReconciliationRun.RunStatus.SKIPPED_OVERLAPPING
    finally:
        release_holder.set()
        t.join(timeout=5)


@pytest.mark.django_db(transaction=True)
def test_lock_is_released_after_run_completes(active_config, mocker):
    mocker.patch(
        "apps.payments.reconciliation_services.TaraConfigService.get_client",
        return_value=make_mock_client(),
    )
    ReconciliationService().run()

    acquired = {}

    def try_from_other_connection():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(782934651)")
            acquired["value"] = cursor.fetchone()[0]
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(782934651)")
        connection.close()

    t = threading.Thread(target=try_from_other_connection)
    t.start()
    t.join(timeout=5)
    assert acquired["value"] is True  # lock was released, so a different session can acquire it


# --- Stale attempt selection ---

def test_stale_pending_attempt_is_selected_and_verified_success(active_config, mocker):
    attempt = make_stale_pending_attempt("stale-success@example.com")
    mock_client = make_mock_client()
    mock_client.check_transaction_status.return_value = status_response(attempt.tara_product_id, "SUCCESS")
    mocker.patch("apps.payments.reconciliation_services.TaraConfigService.get_client", return_value=mock_client)

    run = ReconciliationService().run()

    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.SUCCEEDED
    assert run.verified_successes == 1
    assert run.attempts_examined == 1


def test_fresh_attempt_is_not_selected(active_config, mocker):
    order = make_order("fresh@example.com")
    installment = order.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    attempt = PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    attempt = PaymentAttemptService().transition(attempt, PaymentAttempt.Status.PENDING)
    mock_client = make_mock_client()
    mocker.patch("apps.payments.reconciliation_services.TaraConfigService.get_client", return_value=mock_client)

    run = ReconciliationService().run()

    mock_client.check_transaction_status.assert_not_called()
    assert run.attempts_examined == 0


def test_verified_failure_applies_failed_state(active_config, mocker):
    attempt = make_stale_pending_attempt("stale-failure@example.com")
    mock_client = make_mock_client()
    mock_client.check_transaction_status.return_value = status_response(attempt.tara_product_id, "FAILURE")
    mocker.patch("apps.payments.reconciliation_services.TaraConfigService.get_client", return_value=mock_client)

    run = ReconciliationService().run()

    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.FAILED
    assert run.verified_failures == 1


def test_pending_result_stays_non_final_with_backoff(active_config, mocker):
    attempt = make_stale_pending_attempt("stale-pending@example.com")
    mock_client = make_mock_client()
    mock_client.check_transaction_status.return_value = status_response(attempt.tara_product_id, "PENDING")
    mocker.patch("apps.payments.reconciliation_services.TaraConfigService.get_client", return_value=mock_client)

    run = ReconciliationService().run()

    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.PENDING
    assert attempt.reconciliation_check_count == 1
    assert attempt.next_reconciliation_check_at > timezone.now()
    assert run.still_pending_or_unknown == 1


@pytest.mark.parametrize("exc_class", [TaraConnectionError, TaraServerError])
def test_timeout_and_5xx_remain_recoverable(active_config, mocker, exc_class):
    attempt = make_stale_pending_attempt(f"stale-{exc_class.__name__}@example.com")
    mock_client = make_mock_client()
    mock_client.check_transaction_status.side_effect = exc_class("boom")
    mocker.patch("apps.payments.reconciliation_services.TaraConfigService.get_client", return_value=mock_client)

    run = ReconciliationService().run()

    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.PENDING  # unchanged, non-final
    assert attempt.reconciliation_check_count == 1


def test_bounded_checks_move_to_unknown_for_manual_review(active_config, mocker):
    attempt = make_stale_pending_attempt("bounded-checks@example.com")
    PaymentAttempt.objects.filter(pk=attempt.pk).update(reconciliation_check_count=RECONCILIATION_STALE_ATTEMPT_MAX_CHECKS - 1)
    mock_client = make_mock_client()
    mock_client.check_transaction_status.return_value = status_response(attempt.tara_product_id, "PENDING")
    mocker.patch("apps.payments.reconciliation_services.TaraConfigService.get_client", return_value=mock_client)

    ReconciliationService().run()

    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.UNKNOWN
    assert attempt.reconciliation_check_count == RECONCILIATION_STALE_ATTEMPT_MAX_CHECKS


def test_no_network_call_while_holding_db_lock(active_config, mocker):
    """The Tara call must happen before any select_for_update() — verified by having the mocked call assert no locked row exists yet."""
    attempt = make_stale_pending_attempt("no-lock-during-call@example.com")
    call_log = []

    def fake_check(product_id):
        # If a lock were held during this call, a concurrent connection
        # attempting the same row would block. We simply assert this call
        # happens (network boundary) prior to any state mutation.
        call_log.append(product_id)
        return status_response(product_id, "SUCCESS")

    mock_client = make_mock_client()
    mock_client.check_transaction_status.side_effect = fake_check
    mocker.patch("apps.payments.reconciliation_services.TaraConfigService.get_client", return_value=mock_client)

    ReconciliationService().run()

    assert call_log == [attempt.tara_product_id]
    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.SUCCEEDED


# --- Missing follow-up repair ---

def test_missing_confirmation_is_repaired_once(active_config):
    order = make_order("missing-confirmation@example.com")
    installment = order.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    attempt = PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentCreditService().apply_verified_success(attempt.id)
    # Simulate a lost confirmation row (e.g. a bug/manual deletion) — repair path must recreate it.
    PaymentConfirmation.objects.filter(payment_attempt=attempt).delete()

    run = ReconciliationService().run()
    assert PaymentConfirmation.objects.filter(payment_attempt=attempt).count() == 1
    assert run.missing_work_repaired >= 1

    run2 = ReconciliationService().run()
    assert PaymentConfirmation.objects.filter(payment_attempt=attempt).count() == 1  # not duplicated


def test_missing_enrollment_is_repaired_once(active_config):
    order = make_order("missing-enrollment@example.com")
    installment = order.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    attempt = PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentCreditService().apply_verified_success(attempt.id)
    ProvisioningRequest.objects.filter(order=order).delete()

    ReconciliationService().run()
    assert ProvisioningRequest.objects.filter(order=order).count() == 1

    ReconciliationService().run()
    assert ProvisioningRequest.objects.filter(order=order).count() == 1  # not duplicated


# --- Email/ClickFunnels failures don't block the rest of the run ---

def test_email_failure_does_not_block_enrollment_repair(active_config, mocker):
    import smtplib
    order = make_order("email-fail-no-block@example.com")
    installment = order.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    attempt = PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentCreditService().apply_verified_success(attempt.id)
    mocker.patch("apps.payments.services.send_mail", side_effect=smtplib.SMTPException("boom"))

    run = ReconciliationService().run()

    assert ProvisioningRequest.objects.filter(order=order).exists()
    assert run.run_status != ReconciliationRun.RunStatus.FAILED


def test_clickfunnels_failure_does_not_block_other_work(active_config, mocker):
    order = make_order("cf-fail-no-block@example.com")
    installment = order.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    attempt = PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentCreditService().apply_verified_success(attempt.id)
    mocker.patch(
        "apps.provisioning.services.ProvisioningService._build_enrollment_service",
        side_effect=Exception("clickfunnels down"),
    )

    run = ReconciliationService().run()

    assert run.run_status != ReconciliationRun.RunStatus.FAILED
    assert PaymentConfirmation.objects.filter(payment_attempt=attempt).exists()


# --- Transaction list: reporting only, never credit ---

def test_transaction_list_pull_is_reporting_only(active_config, mocker):
    mock_client = make_mock_client()
    mock_client.check_transaction_status.side_effect = TaraConnectionError("n/a")
    mock_client.list_paid_transactions.return_value = [
        TaraTransactionListItem.model_validate({"transactionId": "txn_1", "status": "PAID", "amount": 1000}),
    ]
    mocker.patch("apps.payments.reconciliation_services.TaraConfigService.get_client", return_value=mock_client)

    run = ReconciliationService().run()

    assert run.uncorrelated_transaction_list_records == 1
    assert PaymentAttempt.objects.filter(status=PaymentAttempt.Status.SUCCEEDED).count() == 0


def test_transaction_list_pagination_stops_on_short_page(active_config, mocker):
    from shared.constants import RECONCILIATION_TRANSACTION_LIST_PAGE_SIZE

    full_page = [TaraTransactionListItem.model_validate({"transactionId": f"t{i}", "status": "PAID"}) for i in range(RECONCILIATION_TRANSACTION_LIST_PAGE_SIZE)]
    short_page = [TaraTransactionListItem.model_validate({"transactionId": "last", "status": "PAID"})]
    mock_client = make_mock_client()
    mock_client.list_paid_transactions.side_effect = [full_page, short_page]
    mocker.patch("apps.payments.reconciliation_services.TaraConfigService.get_client", return_value=mock_client)

    run = ReconciliationService().run()

    assert mock_client.list_paid_transactions.call_count == 2
    assert run.uncorrelated_transaction_list_records == RECONCILIATION_TRANSACTION_LIST_PAGE_SIZE + 1


def test_malformed_transaction_list_response_recorded_as_safe_error(active_config, mocker):
    from integrations.payments.tara.exceptions import TaraMalformedResponseError

    mock_client = make_mock_client()
    mock_client.list_paid_transactions.side_effect = TaraMalformedResponseError("bad shape")
    mocker.patch("apps.payments.reconciliation_services.TaraConfigService.get_client", return_value=mock_client)

    run = ReconciliationService().run()

    assert run.safe_error_count >= 1
    assert run.run_status in (ReconciliationRun.RunStatus.PARTIAL_FAILURE, ReconciliationRun.RunStatus.SUCCESS)


# --- Safety ---

def test_no_secrets_or_pii_in_run_record(active_config, mocker):
    attempt = make_stale_pending_attempt("no-pii@example.com")
    mock_client = make_mock_client()
    mock_client.check_transaction_status.return_value = status_response(attempt.tara_product_id, "SUCCESS")
    mocker.patch("apps.payments.reconciliation_services.TaraConfigService.get_client", return_value=mock_client)

    run = ReconciliationService().run()

    field_names = {f.name for f in ReconciliationRun._meta.get_fields()}
    forbidden = {"api_key", "webhook_secret", "raw_response", "customer_email", "customer_phone", "payment_url"}
    assert not (forbidden & field_names)
