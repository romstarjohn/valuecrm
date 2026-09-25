"""
Hourly reconciliation orchestration (Phase 9, docs/TARA_INTEGRATION_PROJECT.md)
— deliberately separate from services.py (system-driven payment lifecycle)
and admin_services.py (administrator-triggered actions). This module owns
only the scheduled/background reconciliation flow: verifying stale
PaymentAttempts, pulling the transaction list for reporting, driving the
existing Phase 7 confirmation/provisioning workers, and repairing missing
durable follow-up work. It never invents a new state machine or a parallel
confirmation/enrollment implementation — every state change goes through the
same centralized services every other phase already uses.
"""
from django.db import connection, transaction
from django.utils import timezone
from datetime import timedelta

from shared.constants import (
    RECONCILIATION_ADVISORY_LOCK_KEY,
    RECONCILIATION_CONFIRMATION_BATCH_SIZE,
    RECONCILIATION_PROVISIONING_BATCH_SIZE,
    RECONCILIATION_REPAIR_BATCH_SIZE,
    RECONCILIATION_STALE_ATTEMPT_BACKOFF_MINUTES,
    RECONCILIATION_STALE_ATTEMPT_BATCH_SIZE,
    RECONCILIATION_STALE_ATTEMPT_MAX_CHECKS,
    RECONCILIATION_STALE_ATTEMPT_MIN_AGE_MINUTES,
    RECONCILIATION_TRANSACTION_LIST_MAX_PAGES,
    RECONCILIATION_TRANSACTION_LIST_PAGE_SIZE,
)
from shared.logging_utils import get_logger, log_service_start, log_service_success, log_service_failure
from django.db.models import Q

from .models import Installment, Order, PaymentAttempt, PaymentConfirmation, ReconciliationRun
from .services import (
    DuplicatePaymentError,
    InvalidStateTransitionError,
    PaymentAttemptService,
    PaymentConfirmationDeliveryService,
    PaymentConfirmationService,
    PaymentCreditService,
    PaymentIdConflictError,
    TaraConfigService,
)
from integrations.payments.tara.exceptions import (
    TaraClientError,
    TaraConfigurationError,
    TaraConnectionError,
    TaraCredentialError,
    TaraMalformedResponseError,
    TaraServerError,
    TaraTimeoutError,
)
from integrations.payments.tara.schemas import TaraTransactionStatus

logger = get_logger(__name__)

_RETRYABLE_STATUS_CHECK_ERRORS = (
    TaraTimeoutError, TaraConnectionError, TaraServerError, TaraClientError, TaraMalformedResponseError,
)


class ReconciliationService:
    """
    Entry point: `run()`. Safe to invoke as often as desired (e.g. by an
    hourly cron) — an advisory lock ensures only one run is ever active at a
    time, and every sub-operation reuses idempotent, lock-safe services, so a
    duplicate/overlapping invocation (should the lock somehow not apply) can
    never double-credit a payment or double-send a confirmation.
    """

    def run(self) -> ReconciliationRun:
        acquired = self._try_acquire_lock()
        if not acquired:
            log_service_failure(
                logger, "ReconciliationService", "run",
                RuntimeError("another reconciliation run is already in progress"),
            )
            return ReconciliationRun.objects.create(
                started_at=timezone.now(), completed_at=timezone.now(),
                run_status=ReconciliationRun.RunStatus.SKIPPED_OVERLAPPING,
            )

        try:
            try:
                return self._run_locked()
            except Exception as e:
                # A catastrophic failure before/outside _run_locked()'s own
                # per-step error handling (e.g. it couldn't even create the
                # run row) must still yield a definitive FAILED run record —
                # run() never raises, so a crashed run can never permanently
                # wedge anything; the lock is always released in `finally`
                # below regardless.
                log_service_failure(logger, "ReconciliationService", "run", e)
                return ReconciliationRun.objects.create(
                    started_at=timezone.now(), completed_at=timezone.now(),
                    run_status=ReconciliationRun.RunStatus.FAILED, safe_error_count=1,
                )
        finally:
            self._release_lock()

    def _try_acquire_lock(self) -> bool:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [RECONCILIATION_ADVISORY_LOCK_KEY])
            return bool(cursor.fetchone()[0])

    def _release_lock(self) -> None:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [RECONCILIATION_ADVISORY_LOCK_KEY])

    def _run_locked(self) -> ReconciliationRun:
        log_service_start(logger, "ReconciliationService", "run")
        run = ReconciliationRun.objects.create(started_at=timezone.now(), run_status=ReconciliationRun.RunStatus.RUNNING)
        counters = {
            "attempts_examined": 0, "status_checks": 0, "verified_successes": 0, "verified_failures": 0,
            "still_pending_or_unknown": 0, "confirmation_jobs_processed": 0, "confirmation_jobs_sent": 0,
            "confirmation_jobs_failed": 0, "provisioning_jobs_processed": 0, "provisioning_jobs_completed": 0,
            "provisioning_jobs_failed": 0, "missing_work_repaired": 0, "uncorrelated_transaction_list_records": 0,
            "safe_error_count": 0,
        }

        try:
            self._verify_stale_attempts(counters)
            self._pull_transaction_list_for_reporting(counters)
            self._process_confirmations(counters)
            self._process_provisioning(counters)
            self._repair_missing_followups(counters)
            run.run_status = (
                ReconciliationRun.RunStatus.SUCCESS if counters["safe_error_count"] == 0
                else ReconciliationRun.RunStatus.PARTIAL_FAILURE
            )
        except Exception as e:
            counters["safe_error_count"] += 1
            run.run_status = ReconciliationRun.RunStatus.FAILED
            log_service_failure(logger, "ReconciliationService", "run", e)

        run.completed_at = timezone.now()
        for field, value in counters.items():
            setattr(run, field, value)
        run.save()

        log_service_success(logger, "ReconciliationService", "run", run_status=run.run_status)
        return run

    # --- Step 1-6: stale PENDING/UNKNOWN PaymentAttempt verification ---

    def _verify_stale_attempts(self, counters: dict) -> None:
        now = timezone.now()
        cutoff = now - timedelta(minutes=RECONCILIATION_STALE_ATTEMPT_MIN_AGE_MINUTES)
        candidate_ids = list(
            PaymentAttempt.objects.filter(status__in=[PaymentAttempt.Status.PENDING, PaymentAttempt.Status.UNKNOWN])
            .filter(Q(next_reconciliation_check_at__isnull=True) | Q(next_reconciliation_check_at__lte=now))
            .filter(updated_at__lte=cutoff)
            .filter(reconciliation_check_count__lt=RECONCILIATION_STALE_ATTEMPT_MAX_CHECKS)
            .order_by("updated_at")
            .values_list("id", flat=True)[:RECONCILIATION_STALE_ATTEMPT_BATCH_SIZE]
        )
        counters["attempts_examined"] += len(candidate_ids)
        if not candidate_ids:
            return

        try:
            client = TaraConfigService().get_client()
        except (TaraConfigurationError, TaraCredentialError) as e:
            counters["safe_error_count"] += 1
            log_service_failure(logger, "ReconciliationService", "_verify_stale_attempts", e)
            return

        for attempt_id in candidate_ids:
            try:
                self._verify_one_attempt(attempt_id, client, counters)
            except Exception as e:
                counters["safe_error_count"] += 1
                log_service_failure(logger, "ReconciliationService", "_verify_one_attempt", e, attempt_id=attempt_id)

    def _verify_one_attempt(self, attempt_id: int, client, counters: dict) -> None:
        try:
            attempt = PaymentAttempt.objects.get(pk=attempt_id)
        except PaymentAttempt.DoesNotExist:
            return
        if attempt.status not in (PaymentAttempt.Status.PENDING, PaymentAttempt.Status.UNKNOWN):
            return  # resolved before we got to it (e.g. a concurrent webhook)

        product_id = attempt.tara_product_id
        counters["status_checks"] += 1

        # Network call happens here — deliberately outside any DB transaction/lock.
        try:
            status_response = client.check_transaction_status(product_id)
            mismatch = status_response.product_id != product_id
        except _RETRYABLE_STATUS_CHECK_ERRORS:
            status_response = None
            mismatch = False

        if status_response is None or mismatch:
            self._bump_check_count(attempt_id)
            counters["still_pending_or_unknown"] += 1
            return

        normalized = status_response.normalized_status
        if normalized == TaraTransactionStatus.SUCCESS:
            try:
                PaymentCreditService().apply_verified_success(attempt_id)
                counters["verified_successes"] += 1
            except (InvalidStateTransitionError, PaymentIdConflictError, DuplicatePaymentError):
                counters["still_pending_or_unknown"] += 1
        elif normalized == TaraTransactionStatus.FAILURE:
            try:
                PaymentCreditService().apply_verified_failure(attempt_id)
                counters["verified_failures"] += 1
            except InvalidStateTransitionError:
                counters["still_pending_or_unknown"] += 1
        else:
            self._bump_check_count(attempt_id)
            counters["still_pending_or_unknown"] += 1

    def _bump_check_count(self, attempt_id: int) -> None:
        """Short transaction: reload, lock, confirm still eligible, apply idempotently — steps 3-6 of the brief's per-candidate flow."""
        with transaction.atomic():
            attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt_id)
            if attempt.status not in (PaymentAttempt.Status.PENDING, PaymentAttempt.Status.UNKNOWN):
                return
            new_count = attempt.reconciliation_check_count + 1
            backoff_index = min(new_count - 1, len(RECONCILIATION_STALE_ATTEMPT_BACKOFF_MINUTES) - 1)
            attempt.reconciliation_check_count = new_count
            attempt.next_reconciliation_check_at = now = timezone.now() + timedelta(
                minutes=RECONCILIATION_STALE_ATTEMPT_BACKOFF_MINUTES[backoff_index]
            )
            attempt.save(update_fields=["reconciliation_check_count", "next_reconciliation_check_at", "updated_at"])

            if new_count >= RECONCILIATION_STALE_ATTEMPT_MAX_CHECKS and attempt.status == PaymentAttempt.Status.PENDING:
                # Bounded verification attempts exhausted — leave it for manual
                # review via the existing Phase 8 "Check Tara Status" admin
                # action rather than auto-checking forever. UNKNOWN is the
                # existing state for "provider lookup indeterminate."
                PaymentAttemptService().transition(attempt, PaymentAttempt.Status.UNKNOWN)

    # --- Transaction-list pull: reporting/manual review only, never credit ---

    def _pull_transaction_list_for_reporting(self, counters: dict) -> None:
        try:
            client = TaraConfigService().get_client()
        except (TaraConfigurationError, TaraCredentialError) as e:
            counters["safe_error_count"] += 1
            log_service_failure(logger, "ReconciliationService", "_pull_transaction_list_for_reporting", e)
            return

        start = 0
        for _ in range(RECONCILIATION_TRANSACTION_LIST_MAX_PAGES):
            try:
                items = client.list_paid_transactions(start=start, size=RECONCILIATION_TRANSACTION_LIST_PAGE_SIZE)
            except Exception as e:
                counters["safe_error_count"] += 1
                log_service_failure(logger, "ReconciliationService", "_pull_transaction_list_for_reporting", e)
                return
            # No productId in the documented response shape (see
            # TaraTransactionListItem's docstring) — every item pulled here is
            # by construction uncorrelated to a specific PaymentAttempt, and
            # is counted for manual review only. Never touches PaymentCreditService.
            counters["uncorrelated_transaction_list_records"] += len(items)
            if len(items) < RECONCILIATION_TRANSACTION_LIST_PAGE_SIZE:
                return
            start += RECONCILIATION_TRANSACTION_LIST_PAGE_SIZE

    # --- Steps 6-7: drive the existing Phase 7 workers ---

    def _process_confirmations(self, counters: dict) -> None:
        service = PaymentConfirmationDeliveryService()
        now = timezone.now()
        pending_ids = list(
            PaymentConfirmation.objects.filter(status__in=service._CLAIMABLE_STATUSES)
            .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
            .values_list("id", flat=True)[:RECONCILIATION_CONFIRMATION_BATCH_SIZE]
        )
        counters["confirmation_jobs_processed"] += len(pending_ids)
        if not pending_ids:
            return
        try:
            service.process_pending(limit=RECONCILIATION_CONFIRMATION_BATCH_SIZE)
        except Exception as e:
            # A failed email must never fail the whole hourly run — process_pending()
            # already isolates per-item failures internally; this is defense in depth.
            counters["safe_error_count"] += 1
            log_service_failure(logger, "ReconciliationService", "_process_confirmations", e)
        counters["confirmation_jobs_sent"] += PaymentConfirmation.objects.filter(
            id__in=pending_ids, status=PaymentConfirmation.Status.SENT,
        ).count()
        counters["confirmation_jobs_failed"] += PaymentConfirmation.objects.filter(
            id__in=pending_ids, status__in=[PaymentConfirmation.Status.FAILED, PaymentConfirmation.Status.MANUAL_REVIEW],
        ).count()

    def _process_provisioning(self, counters: dict) -> None:
        from apps.provisioning.models import ProvisioningRequest
        from apps.provisioning.services import ProvisioningService

        service = ProvisioningService()
        pending_ids = list(
            ProvisioningRequest.objects.filter(status__in=service._CLAIMABLE_STATUSES)
            .values_list("id", flat=True)[:RECONCILIATION_PROVISIONING_BATCH_SIZE]
        )
        counters["provisioning_jobs_processed"] += len(pending_ids)
        if not pending_ids:
            return
        try:
            service.process_pending(limit=RECONCILIATION_PROVISIONING_BATCH_SIZE)
        except Exception as e:
            # A failed ClickFunnels call must never fail the whole hourly run —
            # process_pending()/execute() already isolate per-item failures;
            # this is defense in depth only.
            counters["safe_error_count"] += 1
            log_service_failure(logger, "ReconciliationService", "_process_provisioning", e)
        counters["provisioning_jobs_completed"] += ProvisioningRequest.objects.filter(
            id__in=pending_ids, status=ProvisioningRequest.Status.COMPLETED,
        ).count()
        counters["provisioning_jobs_failed"] += ProvisioningRequest.objects.filter(
            id__in=pending_ids, status__in=[ProvisioningRequest.Status.FAILED, ProvisioningRequest.Status.MANUAL_REVIEW],
        ).count()

    # --- Steps 8-9: detect and repair missing durable follow-up work ---

    def _repair_missing_followups(self, counters: dict) -> None:
        from apps.provisioning.services import ProvisioningService

        succeeded_without_confirmation = (
            PaymentAttempt.objects.filter(status=PaymentAttempt.Status.SUCCEEDED)
            .exclude(id__in=PaymentConfirmation.objects.values_list("payment_attempt_id", flat=True))
            .select_related("installment__order")[:RECONCILIATION_REPAIR_BATCH_SIZE]
        )
        for attempt in succeeded_without_confirmation:
            try:
                installment = attempt.installment
                order = installment.order
                PaymentConfirmationService().create_confirmation(attempt, installment, order)
                counters["missing_work_repaired"] += 1
            except Exception as e:
                counters["safe_error_count"] += 1
                log_service_failure(logger, "ReconciliationService", "_repair_missing_followups", e, attempt_id=attempt.id)

        from apps.provisioning.models import ProvisioningRequest

        orders_with_paid_installment = (
            Order.objects.filter(installments__status=Installment.Status.PAID)
            .distinct()
            .exclude(id__in=ProvisioningRequest.objects.filter(order__isnull=False).values_list("order_id", flat=True))
            [:RECONCILIATION_REPAIR_BATCH_SIZE]
        )
        for order in orders_with_paid_installment:
            try:
                if order.is_access_eligible:
                    ProvisioningService().create_request_from_order(order)
                    counters["missing_work_repaired"] += 1
            except Exception as e:
                counters["safe_error_count"] += 1
                log_service_failure(logger, "ReconciliationService", "_repair_missing_followups", e, order_id=order.id)
