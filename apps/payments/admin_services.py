"""
Administrative/manual operations (Phase 8, docs/TARA_INTEGRATION_PROJECT.md) —
deliberately separate from services.py's system-driven payment lifecycle
(PaymentCreditService, WebhookProcessingService, CheckoutService, etc.). Every
method here is triggered only by an authenticated administrator through a
permission-gated, reason-required admin action (see apps/payments/admin.py
and apps/payments/admin_actions.py), never by a system/webhook path.

Common shape: reload-and-lock the target by primary key inside a short
transaction, validate the requested change against the SAME state-transition
services the rest of the app already uses (OrderService/InstallmentService/
PaymentAttemptService/PaymentCreditService), and write an AdminAuditLog row
in that same transaction — success and rejection alike, where practical.
Never rewrites a provider fact, a SUCCEEDED PaymentAttempt, a PAID
Installment, or PaymentConfirmation delivery truth.

IMPORTANT: a rejection is recorded as an audit row and THEN raised as
AdminActionError — but the audit write and the raise must not be inside the
same `transaction.atomic()` block. Raising out of an atomic block rolls back
everything written inside it, including the audit row the whole point was to
keep. Every method below therefore records the rejection audit row, lets the
`with transaction.atomic():` block exit normally (so it commits), and only
raises AFTER that block has exited.
"""
from decimal import Decimal
from typing import Optional

from django.db import transaction
from django.utils import timezone

from shared.logging_utils import get_logger, log_service_start, log_service_success, log_service_failure
from apps.configuration.services import ConfigurationService
from apps.contacts.services import ContactService
from apps.enrollments.models import EnrollmentAttempt
from apps.enrollments.services import EnrollmentService
from apps.provisioning.models import ProvisioningAttempt, ProvisioningRequest
from integrations.clickfunnels.client import ClickFunnelsClient
from .models import (
    AdminAuditLog,
    Installment,
    Order,
    PaymentAttempt,
    PaymentConfirmation,
    TaraWebhookEvent,
)
from .services import (
    DuplicatePaymentError,
    InstallmentService,
    InvalidStateTransitionError,
    OrderService,
    PaymentAttemptService,
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


class AdminActionError(Exception):
    """Raised when a requested administrative action is rejected (invalid state, not found, etc.)."""


class AdminAuditService:
    """
    Writes AdminAuditLog rows. Always call this from inside the same
    transaction as the state change it documents (or, for a rejected/no-op
    attempt, as the last write in that transaction before it commits) — never
    from a background job or after the fact.
    """

    def record(
        self, *, administrator, action_type: str, target_type: str, target_reference: str,
        reason: str, outcome_category: str,
        order: Optional[Order] = None, installment: Optional[Installment] = None,
        payment_attempt: Optional[PaymentAttempt] = None,
        webhook_event: Optional[TaraWebhookEvent] = None,
        previous_state: str = "", resulting_state: str = "",
        request=None,
    ) -> AdminAuditLog:
        metadata = {}
        if request is not None:
            remote_addr = request.META.get("REMOTE_ADDR")
            if remote_addr:
                metadata["remote_addr"] = remote_addr

        administrator_id = getattr(administrator, "pk", None)
        entry = AdminAuditLog.objects.create(
            administrator_id=administrator_id,
            administrator_username=getattr(administrator, "username", "") or "",
            action_type=action_type,
            target_type=target_type,
            target_reference=target_reference,
            order=order,
            installment=installment,
            payment_attempt=payment_attempt,
            webhook_event=webhook_event,
            previous_state=previous_state,
            resulting_state=resulting_state,
            reason=reason,
            outcome_category=outcome_category,
            request_metadata=metadata,
        )
        log_service_success(
            logger, "AdminAuditService", "record",
            action_type=action_type, outcome_category=outcome_category,
        )
        return entry


def _safe_reference(obj) -> str:
    reference = getattr(obj, "reference", None)
    if reference:
        return str(reference)
    return f"{obj.__class__.__name__}#{obj.pk}"


_ORDER_ELIGIBLE_INSTALLMENT_STATUSES = {
    Installment.Status.SCHEDULED, Installment.Status.DUE,
    Installment.Status.PENDING, Installment.Status.FAILED,
}
_NON_TERMINAL_ATTEMPT_STATUSES = {
    PaymentAttempt.Status.CREATED, PaymentAttempt.Status.LINK_CREATED,
    PaymentAttempt.Status.PENDING, PaymentAttempt.Status.UNKNOWN,
}


class OrderAdministrationService:
    """Centralized administrative operations on Order/Installment (Phase 8)."""

    def cancel_order(self, order_id: int, reason: str, administrator, request=None) -> Order:
        """
        Row-locks the Order and its Installments/PaymentAttempts. Idempotent
        when already CANCELLED. Never alters a PAID installment or a
        SUCCEEDED attempt — only SCHEDULED/DUE/PENDING/FAILED installments
        (and their non-terminal attempts) are closed out. Non-terminal
        attempts are moved to EXPIRED — PaymentAttempt has no CANCELLED status
        of its own (see PaymentAttempt.Status); EXPIRED is the closest
        existing terminal state for "this attempt will not be pursued
        further," avoiding a schema change to the Phase 3 state machine for
        this phase. Issues no refund and calls neither Tara nor ClickFunnels.
        """
        log_service_start(logger, "OrderAdministrationService", "cancel_order", order_id=order_id)
        error_message = None
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=order_id)
            previous_status = order.status

            if order.status == Order.Status.CANCELLED:
                AdminAuditService().record(
                    administrator=administrator, action_type=AdminAuditLog.ActionType.CANCEL_ORDER,
                    target_type=AdminAuditLog.TargetType.ORDER, target_reference=_safe_reference(order),
                    order=order, previous_state=previous_status, resulting_state=order.status,
                    reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.NO_OP_ALREADY_IN_STATE,
                    request=request,
                )
                return order

            try:
                order = OrderService().transition(order, Order.Status.CANCELLED)
            except InvalidStateTransitionError:
                AdminAuditService().record(
                    administrator=administrator, action_type=AdminAuditLog.ActionType.CANCEL_ORDER,
                    target_type=AdminAuditLog.TargetType.ORDER, target_reference=_safe_reference(order),
                    order=order, previous_state=previous_status, resulting_state=previous_status,
                    reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.REJECTED_INVALID_STATE,
                    request=request,
                )
                error_message = f"Order cannot be cancelled from status {previous_status}."
            else:
                installment_service = InstallmentService()
                attempt_service = PaymentAttemptService()
                for installment in order.installments.select_for_update():
                    if installment.status not in _ORDER_ELIGIBLE_INSTALLMENT_STATUSES:
                        continue  # PAID/WAIVED/CANCELLED — never touched
                    for attempt in installment.payment_attempts.select_for_update():
                        if attempt.status in _NON_TERMINAL_ATTEMPT_STATUSES:
                            attempt_service.transition(attempt, PaymentAttempt.Status.EXPIRED)
                    installment_service.transition(installment, Installment.Status.CANCELLED)

                AdminAuditService().record(
                    administrator=administrator, action_type=AdminAuditLog.ActionType.CANCEL_ORDER,
                    target_type=AdminAuditLog.TargetType.ORDER, target_reference=_safe_reference(order),
                    order=order, previous_state=previous_status, resulting_state=order.status,
                    reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.SUCCESS,
                    request=request,
                )
                log_service_success(logger, "OrderAdministrationService", "cancel_order", order_id=order.id)

        if error_message is not None:
            raise AdminActionError(error_message)
        return order

    def cancel_installment(self, installment_id: int, reason: str, administrator, request=None) -> Installment:
        """Cancels one installment only — never a PAID one, never one with a SUCCEEDED attempt."""
        log_service_start(logger, "OrderAdministrationService", "cancel_installment", installment_id=installment_id)
        error_message = None
        with transaction.atomic():
            installment = Installment.objects.select_for_update().get(pk=installment_id)
            order = Order.objects.select_for_update().get(pk=installment.order_id)
            previous_status = installment.status

            if installment.status == Installment.Status.CANCELLED:
                AdminAuditService().record(
                    administrator=administrator, action_type=AdminAuditLog.ActionType.CANCEL_INSTALLMENT,
                    target_type=AdminAuditLog.TargetType.INSTALLMENT, target_reference=_safe_reference(installment),
                    order=order, installment=installment, previous_state=previous_status, resulting_state=installment.status,
                    reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.NO_OP_ALREADY_IN_STATE,
                    request=request,
                )
                return installment

            has_succeeded_attempt = installment.payment_attempts.filter(status=PaymentAttempt.Status.SUCCEEDED).exists()
            if installment.status == Installment.Status.PAID or has_succeeded_attempt:
                AdminAuditService().record(
                    administrator=administrator, action_type=AdminAuditLog.ActionType.CANCEL_INSTALLMENT,
                    target_type=AdminAuditLog.TargetType.INSTALLMENT, target_reference=_safe_reference(installment),
                    order=order, installment=installment, previous_state=previous_status, resulting_state=previous_status,
                    reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.REJECTED_INVALID_STATE,
                    request=request,
                )
                error_message = "A paid installment (or one with a succeeded payment attempt) cannot be cancelled."
            else:
                attempt_service = PaymentAttemptService()
                for attempt in installment.payment_attempts.select_for_update():
                    if attempt.status in _NON_TERMINAL_ATTEMPT_STATUSES:
                        attempt_service.transition(attempt, PaymentAttempt.Status.EXPIRED)

                try:
                    installment = InstallmentService().transition(installment, Installment.Status.CANCELLED)
                except InvalidStateTransitionError as e:
                    AdminAuditService().record(
                        administrator=administrator, action_type=AdminAuditLog.ActionType.CANCEL_INSTALLMENT,
                        target_type=AdminAuditLog.TargetType.INSTALLMENT, target_reference=_safe_reference(installment),
                        order=order, installment=installment, previous_state=previous_status, resulting_state=previous_status,
                        reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.REJECTED_INVALID_STATE,
                        request=request,
                    )
                    error_message = str(e)
                else:
                    AdminAuditService().record(
                        administrator=administrator, action_type=AdminAuditLog.ActionType.CANCEL_INSTALLMENT,
                        target_type=AdminAuditLog.TargetType.INSTALLMENT, target_reference=_safe_reference(installment),
                        order=order, installment=installment, previous_state=previous_status, resulting_state=installment.status,
                        reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.SUCCESS,
                        request=request,
                    )
                    log_service_success(logger, "OrderAdministrationService", "cancel_installment", installment_id=installment.id)

        if error_message is not None:
            raise AdminActionError(error_message)
        return installment

    def waive_installment(self, installment_id: int, reason: str, administrator, request=None) -> Installment:
        """
        Internal business decision, not a provider payment (Phase 8). Only
        unpaid installments with no SUCCEEDED attempt may be waived;
        paid_amount is never set (stays None/zero — WAIVED is not "paid").
        Creates no PaymentConfirmation and no Tara payment history. Recomputes
        Order completion/eligibility with the existing rules and, if that
        newly makes a FULL_PAYMENT order eligible, creates exactly one
        durable provisioning request via the existing (idempotent) Phase 7
        service — never a PaymentConfirmation.
        """
        log_service_start(logger, "OrderAdministrationService", "waive_installment", installment_id=installment_id)
        error_message = None
        with transaction.atomic():
            installment = Installment.objects.select_for_update().get(pk=installment_id)
            order = Order.objects.select_for_update().get(pk=installment.order_id)
            previous_status = installment.status

            if installment.status == Installment.Status.WAIVED:
                AdminAuditService().record(
                    administrator=administrator, action_type=AdminAuditLog.ActionType.WAIVE_INSTALLMENT,
                    target_type=AdminAuditLog.TargetType.INSTALLMENT, target_reference=_safe_reference(installment),
                    order=order, installment=installment, previous_state=previous_status, resulting_state=installment.status,
                    reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.NO_OP_ALREADY_IN_STATE,
                    request=request,
                )
                return installment

            has_succeeded_attempt = installment.payment_attempts.filter(status=PaymentAttempt.Status.SUCCEEDED).exists()
            if installment.status == Installment.Status.PAID or has_succeeded_attempt:
                AdminAuditService().record(
                    administrator=administrator, action_type=AdminAuditLog.ActionType.WAIVE_INSTALLMENT,
                    target_type=AdminAuditLog.TargetType.INSTALLMENT, target_reference=_safe_reference(installment),
                    order=order, installment=installment, previous_state=previous_status, resulting_state=previous_status,
                    reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.REJECTED_INVALID_STATE,
                    request=request,
                )
                error_message = "A paid installment (or one with a succeeded payment attempt) cannot be waived."
            else:
                attempt_service = PaymentAttemptService()
                for attempt in installment.payment_attempts.select_for_update():
                    if attempt.status in _NON_TERMINAL_ATTEMPT_STATUSES:
                        attempt_service.transition(attempt, PaymentAttempt.Status.EXPIRED)

                try:
                    installment = InstallmentService().transition(installment, Installment.Status.WAIVED)
                except InvalidStateTransitionError as e:
                    AdminAuditService().record(
                        administrator=administrator, action_type=AdminAuditLog.ActionType.WAIVE_INSTALLMENT,
                        target_type=AdminAuditLog.TargetType.INSTALLMENT, target_reference=_safe_reference(installment),
                        order=order, installment=installment, previous_state=previous_status, resulting_state=previous_status,
                        reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.REJECTED_INVALID_STATE,
                        request=request,
                    )
                    error_message = str(e)
                else:
                    # Reuse the exact Phase 6 completion-recompute rules — never duplicate them.
                    PaymentCreditService()._update_order_status(order)
                    order.refresh_from_db()

                    if order.is_access_eligible:
                        from apps.provisioning.services import ProvisioningService

                        ProvisioningService().create_request_from_order(order)

                    AdminAuditService().record(
                        administrator=administrator, action_type=AdminAuditLog.ActionType.WAIVE_INSTALLMENT,
                        target_type=AdminAuditLog.TargetType.INSTALLMENT, target_reference=_safe_reference(installment),
                        order=order, installment=installment, previous_state=previous_status, resulting_state=installment.status,
                        reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.SUCCESS,
                        request=request,
                    )
                    log_service_success(logger, "OrderAdministrationService", "waive_installment", installment_id=installment.id)

        if error_message is not None:
            raise AdminActionError(error_message)
        return installment

    def apply_manual_disposition(self, order_id: int, disposition: str, reason: str, administrator, request=None) -> Order:
        """
        Sets Order.manual_disposition only — never touches PaymentAttempt/
        Installment/PaymentConfirmation state, never a provider fact.
        """
        log_service_start(logger, "OrderAdministrationService", "apply_manual_disposition", order_id=order_id)
        valid_values = {choice for choice, _ in Order.ManualDisposition.choices}
        error_message = None
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=order_id)
            previous_disposition = order.manual_disposition

            if disposition not in valid_values:
                AdminAuditService().record(
                    administrator=administrator, action_type=AdminAuditLog.ActionType.APPLY_MANUAL_DISPOSITION,
                    target_type=AdminAuditLog.TargetType.ORDER, target_reference=_safe_reference(order),
                    order=order, previous_state=previous_disposition, resulting_state=previous_disposition,
                    reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.REJECTED_INVALID_STATE,
                    request=request,
                )
                error_message = f"'{disposition}' is not a recognized disposition."
            elif previous_disposition == disposition:
                AdminAuditService().record(
                    administrator=administrator, action_type=AdminAuditLog.ActionType.APPLY_MANUAL_DISPOSITION,
                    target_type=AdminAuditLog.TargetType.ORDER, target_reference=_safe_reference(order),
                    order=order, previous_state=previous_disposition, resulting_state=disposition,
                    reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.NO_OP_ALREADY_IN_STATE,
                    request=request,
                )
            else:
                order.manual_disposition = disposition
                order.save(update_fields=["manual_disposition", "updated_at"])

                AdminAuditService().record(
                    administrator=administrator, action_type=AdminAuditLog.ActionType.APPLY_MANUAL_DISPOSITION,
                    target_type=AdminAuditLog.TargetType.ORDER, target_reference=_safe_reference(order),
                    order=order, previous_state=previous_disposition, resulting_state=disposition,
                    reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.SUCCESS,
                    request=request,
                )
                log_service_success(logger, "OrderAdministrationService", "apply_manual_disposition", order_id=order.id)

        if error_message is not None:
            raise AdminActionError(error_message)
        return order


CHECKABLE_ATTEMPT_STATUSES = (
    PaymentAttempt.Status.LINK_CREATED, PaymentAttempt.Status.PENDING, PaymentAttempt.Status.UNKNOWN,
    PaymentAttempt.Status.FAILED, PaymentAttempt.Status.EXPIRED,
)


class PaymentAttemptAdministrationService:
    """Administrative operations on PaymentAttempt (Phase 8)."""

    def check_tara_status(self, attempt_id: int, reason: str, administrator, request=None) -> PaymentAttempt:
        """
        Synchronous, admin-triggered server-to-server status check — mirrors
        WebhookProcessingService._verify_and_apply's Tara call/classification
        (same client, same exception taxonomy, same centralized services for
        applying a result), but the Tara call itself is a direct, on-demand
        admin operation, not a durable/queued one — consistent with how the
        webhook path itself already calls Tara synchronously within its own
        request/response cycle. LINK_CREATED/PENDING/UNKNOWN/FAILED/EXPIRED
        attempts are eligible. FAILED/EXPIRED are included because a customer
        can still pay on the same Tara link after a first failed try — Tara's
        verified SUCCESS then reopens the attempt (see
        PaymentCreditService._reopen_for_late_success). Of the remaining
        states, LINK_CREATED is also — LINK_CREATED is included because a customer can complete
        payment on a generated link before Tara's webhook ever arrives (or
        if it's lost entirely), leaving the attempt stuck with nothing to
        surface it for reconciliation otherwise; PaymentAttempt's own
        transition table already allows LINK_CREATED -> SUCCEEDED/FAILED
        directly, so no state-machine change was needed, only this
        eligibility gate. Never creates a new PaymentAttempt or payment
        link. Never marks paid from administrator input — only Tara's own
        response can do that, via the same PaymentCreditService the webhook
        path uses.
        """
        log_service_start(logger, "PaymentAttemptAdministrationService", "check_tara_status", attempt_id=attempt_id)

        attempt = PaymentAttempt.objects.select_related("installment__order").get(pk=attempt_id)
        if attempt.status not in CHECKABLE_ATTEMPT_STATUSES:
            AdminAuditService().record(
                administrator=administrator, action_type=AdminAuditLog.ActionType.CHECK_TARA_STATUS,
                target_type=AdminAuditLog.TargetType.PAYMENT_ATTEMPT, target_reference=_safe_reference(attempt),
                order=attempt.installment.order, installment=attempt.installment, payment_attempt=attempt,
                previous_state=attempt.status, resulting_state=attempt.status,
                reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.REJECTED_INVALID_STATE,
                request=request,
            )
            raise AdminActionError("Only LINK_CREATED, PENDING, UNKNOWN, FAILED, or EXPIRED payment attempts can be checked.")

        previous_status = attempt.status

        try:
            client = TaraConfigService().get_client()
        except (TaraConfigurationError, TaraCredentialError) as e:
            log_service_failure(logger, "PaymentAttemptAdministrationService", "check_tara_status", e, attempt_id=attempt_id)
            AdminAuditService().record(
                administrator=administrator, action_type=AdminAuditLog.ActionType.CHECK_TARA_STATUS,
                target_type=AdminAuditLog.TargetType.PAYMENT_ATTEMPT, target_reference=_safe_reference(attempt),
                order=attempt.installment.order, installment=attempt.installment, payment_attempt=attempt,
                previous_state=previous_status, resulting_state=previous_status,
                reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.FAILED,
                request=request,
            )
            raise AdminActionError("Tara is not configured; status could not be checked.") from e

        try:
            status_response = client.check_transaction_status(attempt.tara_product_id)
        except (TaraTimeoutError, TaraConnectionError, TaraServerError, TaraClientError, TaraMalformedResponseError, Exception) as e:
            # Broad `Exception` deliberately included, not just the documented
            # Tara exception taxonomy — any unexpected error from the client
            # call must still leave the attempt non-final and must never leak
            # str(e) (which could contain a raw provider response body) to the
            # admin UI or the audit log. Only a safe, fixed message is used.
            log_service_failure(logger, "PaymentAttemptAdministrationService", "check_tara_status", e, attempt_id=attempt_id)
            AdminAuditService().record(
                administrator=administrator, action_type=AdminAuditLog.ActionType.CHECK_TARA_STATUS,
                target_type=AdminAuditLog.TargetType.PAYMENT_ATTEMPT, target_reference=_safe_reference(attempt),
                order=attempt.installment.order, installment=attempt.installment, payment_attempt=attempt,
                previous_state=previous_status, resulting_state=previous_status,
                reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.PROVIDER_NON_FINAL,
                request=request,
            )
            return attempt  # non-final — never raised as an error to keep the attempt inspectable, not a hard failure

        if status_response.product_id != attempt.tara_product_id:
            AdminAuditService().record(
                administrator=administrator, action_type=AdminAuditLog.ActionType.CHECK_TARA_STATUS,
                target_type=AdminAuditLog.TargetType.PAYMENT_ATTEMPT, target_reference=_safe_reference(attempt),
                order=attempt.installment.order, installment=attempt.installment, payment_attempt=attempt,
                previous_state=previous_status, resulting_state=previous_status,
                reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.PROVIDER_NON_FINAL,
                request=request,
            )
            return attempt

        normalized = status_response.normalized_status

        def write_audit(applied_attempt: PaymentAttempt, outcome: str) -> None:
            # Invoked from inside PaymentCreditService's own transaction (via
            # on_applied below) so the state change and this audit record
            # commit or roll back together, per the Phase 8 requirement.
            AdminAuditService().record(
                administrator=administrator, action_type=AdminAuditLog.ActionType.CHECK_TARA_STATUS,
                target_type=AdminAuditLog.TargetType.PAYMENT_ATTEMPT, target_reference=_safe_reference(applied_attempt),
                order=applied_attempt.installment.order, installment=applied_attempt.installment, payment_attempt=applied_attempt,
                previous_state=previous_status, resulting_state=applied_attempt.status,
                reason=reason, outcome_category=outcome,
                request=request,
            )

        if normalized == TaraTransactionStatus.SUCCESS:
            try:
                attempt = PaymentCreditService().apply_verified_success(
                    attempt.id,
                    on_applied=lambda a, newly_applied: write_audit(a, AdminAuditLog.OutcomeCategory.SUCCESS),
                )
            except PaymentIdConflictError as e:
                log_service_failure(logger, "PaymentAttemptAdministrationService", "check_tara_status", e, attempt_id=attempt_id)
                attempt.refresh_from_db()
                write_audit(attempt, AdminAuditLog.OutcomeCategory.FAILED)
            except (DuplicatePaymentError, InvalidStateTransitionError) as e:
                log_service_failure(logger, "PaymentAttemptAdministrationService", "check_tara_status", e, attempt_id=attempt_id)
                attempt.refresh_from_db()
                write_audit(attempt, AdminAuditLog.OutcomeCategory.FAILED)
                raise AdminActionError(
                    "Tara confirms this payment, but it could not be credited automatically "
                    "(installment already paid via another attempt, or cancelled/waived). Manual review required."
                ) from e
        elif normalized == TaraTransactionStatus.FAILURE:
            attempt = PaymentCreditService().apply_verified_failure(
                attempt.id, on_applied=lambda a: write_audit(a, AdminAuditLog.OutcomeCategory.SUCCESS),
            )
        else:
            attempt.refresh_from_db()
            write_audit(attempt, AdminAuditLog.OutcomeCategory.PROVIDER_NON_FINAL)

        log_service_success(
            logger, "PaymentAttemptAdministrationService", "check_tara_status",
            attempt_id=attempt.id, normalized_result=normalized.value,
        )
        return attempt


class ReconciliationAdministrationService:
    """
    Administrative resolution of an UNCORRELATED TaraWebhookEvent — a payment
    Tara confirmed that could not be matched to a PaymentAttempt by
    tara_product_id, most commonly a payment made directly in the Tara app
    rather than through our checkout link (docs/PRODUCT_CADRAGE_PMI.md §6
    exception table, "paiement confirmé, utilisateur ou achat introuvable" —
    reserved to the administrator role, not the general gestionnaire).

    Never fabricates a payment: the administrator-confirmed amount is what
    gets credited (PaymentCreditService.apply_verified_success's
    verified_amount), and an amount below the installment's expected_amount
    is refused outright rather than accepted at the wrong figure — the
    cumulative-balance rule a genuine partial-payment attribution would need
    is an open product decision (D3) and is deliberately not implemented here.
    """

    def attribute_webhook_event(
        self, webhook_event_id: int, installment_id: int, confirmed_amount: Decimal,
        reason: str, administrator, request=None,
    ) -> TaraWebhookEvent:
        log_service_start(
            logger, "ReconciliationAdministrationService", "attribute_webhook_event",
            webhook_event_id=webhook_event_id, installment_id=installment_id,
        )
        error_message = None
        with transaction.atomic():
            event = TaraWebhookEvent.objects.select_for_update().get(pk=webhook_event_id)
            installment = Installment.objects.select_for_update().get(pk=installment_id)
            order = Order.objects.select_for_update().get(pk=installment.order_id)
            previous_status = event.processing_status

            if event.processing_status != TaraWebhookEvent.ProcessingStatus.UNCORRELATED:
                error_message = "Seul un événement non rapproché (UNCORRELATED) peut être associé."
            elif installment.status in (Installment.Status.PAID, Installment.Status.CANCELLED, Installment.Status.WAIVED):
                error_message = f"Le versement n°{installment.sequence} est déjà {installment.status} — impossible d'y associer ce paiement."
            elif confirmed_amount < installment.expected_amount:
                error_message = (
                    "Le montant confirmé est inférieur au montant attendu pour ce versement — le rattachement "
                    "d'un paiement partiel n'est pas encore pris en charge (règle de cumul non arrêtée, décision D3)."
                )

            if error_message is not None:
                AdminAuditService().record(
                    administrator=administrator, action_type=AdminAuditLog.ActionType.ATTRIBUTE_PAYMENT,
                    target_type=AdminAuditLog.TargetType.TARA_WEBHOOK_EVENT, target_reference=_safe_reference(event),
                    order=order, installment=installment, webhook_event=event,
                    previous_state=previous_status, resulting_state=previous_status,
                    reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.REJECTED_INVALID_STATE,
                    request=request,
                )
            else:
                # Reuse an existing non-terminal attempt on this installment if
                # our checkout already created one (e.g. it just failed to match
                # this specific webhook for another reason); otherwise this is a
                # genuine direct-to-Tara payment and needs a new attempt row for
                # PaymentCreditService to credit against.
                attempt = installment.payment_attempts.select_for_update().filter(
                    status__in=_NON_TERMINAL_ATTEMPT_STATUSES,
                ).first()
                if attempt is None:
                    attempt = PaymentAttempt.objects.create(
                        installment=installment, expected_amount=installment.expected_amount,
                        currency=installment.currency, status=PaymentAttempt.Status.PENDING,
                    )

                def write_audit(applied_attempt: PaymentAttempt, outcome: str) -> None:
                    AdminAuditService().record(
                        administrator=administrator, action_type=AdminAuditLog.ActionType.ATTRIBUTE_PAYMENT,
                        target_type=AdminAuditLog.TargetType.TARA_WEBHOOK_EVENT, target_reference=_safe_reference(event),
                        order=order, installment=installment, payment_attempt=applied_attempt, webhook_event=event,
                        previous_state=previous_status, resulting_state=TaraWebhookEvent.ProcessingStatus.PROCESSED,
                        reason=reason, outcome_category=outcome, request=request,
                    )

                try:
                    attempt = PaymentCreditService().apply_verified_success(
                        attempt.id, tara_payment_id=(event.tara_payment_id or None), verified_amount=confirmed_amount,
                        on_applied=lambda a, newly_applied: write_audit(a, AdminAuditLog.OutcomeCategory.SUCCESS),
                    )
                except PaymentIdConflictError as e:
                    log_service_failure(
                        logger, "ReconciliationAdministrationService", "attribute_webhook_event", e,
                        webhook_event_id=webhook_event_id,
                    )
                    AdminAuditService().record(
                        administrator=administrator, action_type=AdminAuditLog.ActionType.ATTRIBUTE_PAYMENT,
                        target_type=AdminAuditLog.TargetType.TARA_WEBHOOK_EVENT, target_reference=_safe_reference(event),
                        order=order, installment=installment, webhook_event=event,
                        previous_state=previous_status, resulting_state=previous_status,
                        reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.FAILED,
                        request=request,
                    )
                    error_message = "Ce paymentId Tara est déjà rattaché à une autre tentative de paiement."
                else:
                    event.processing_status = TaraWebhookEvent.ProcessingStatus.PROCESSED
                    event.verification_mode = TaraWebhookEvent.VerificationMode.SERVER_TO_SERVER
                    event.verification_result = TaraWebhookEvent.VerificationResult.SUCCESS
                    event.failure_category = ""
                    event.payment_attempt = attempt
                    event.processed_at = timezone.now()
                    event.save(update_fields=[
                        "processing_status", "verification_mode", "verification_result",
                        "failure_category", "payment_attempt", "processed_at", "updated_at",
                    ])
                    log_service_success(
                        logger, "ReconciliationAdministrationService", "attribute_webhook_event",
                        webhook_event_id=event.id, installment_id=installment.id,
                    )

        if error_message is not None:
            raise AdminActionError(error_message)
        return event


class PaymentConfirmationAdministrationService:
    """Administrative operations on PaymentConfirmation (Phase 8)."""

    def retry_confirmation(self, confirmation_id: int, reason: str, administrator, request=None) -> PaymentConfirmation:
        """
        Returns a FAILED/MANUAL_REVIEW confirmation to PENDING so the existing
        worker (PaymentConfirmationDeliveryService, run via
        process_payment_confirmations) can claim and send it — never sends
        email from this admin transaction. SENT confirmations can never be
        retried. attempt_count is reset to 0 (a fresh start is what makes the
        retry actually claimable/retryable again) — the count and category
        being reset are preserved for audit purposes in this action's own
        AdminAuditLog entry's previous_state.
        """
        log_service_start(logger, "PaymentConfirmationAdministrationService", "retry_confirmation", confirmation_id=confirmation_id)
        error_message = None
        with transaction.atomic():
            confirmation = PaymentConfirmation.objects.select_for_update().get(pk=confirmation_id)
            previous_status = confirmation.status
            previous_state_label = f"{previous_status} (attempts={confirmation.attempt_count}, category={confirmation.last_failure_category or 'n/a'})"

            if confirmation.status == PaymentConfirmation.Status.SENT:
                AdminAuditService().record(
                    administrator=administrator, action_type=AdminAuditLog.ActionType.RETRY_CONFIRMATION,
                    target_type=AdminAuditLog.TargetType.PAYMENT_CONFIRMATION, target_reference=_safe_reference(confirmation),
                    order=confirmation.order, installment=confirmation.installment, payment_attempt=confirmation.payment_attempt,
                    previous_state=previous_state_label, resulting_state=previous_status,
                    reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.REJECTED_INVALID_STATE,
                    request=request,
                )
                error_message = "A SENT confirmation cannot be retried."
            elif confirmation.status not in (PaymentConfirmation.Status.FAILED, PaymentConfirmation.Status.MANUAL_REVIEW):
                AdminAuditService().record(
                    administrator=administrator, action_type=AdminAuditLog.ActionType.RETRY_CONFIRMATION,
                    target_type=AdminAuditLog.TargetType.PAYMENT_CONFIRMATION, target_reference=_safe_reference(confirmation),
                    order=confirmation.order, installment=confirmation.installment, payment_attempt=confirmation.payment_attempt,
                    previous_state=previous_state_label, resulting_state=previous_status,
                    reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.REJECTED_INVALID_STATE,
                    request=request,
                )
                error_message = "Only FAILED or MANUAL_REVIEW confirmations can be retried."
            else:
                recipient = confirmation.contact.email
                if not recipient:
                    AdminAuditService().record(
                        administrator=administrator, action_type=AdminAuditLog.ActionType.RETRY_CONFIRMATION,
                        target_type=AdminAuditLog.TargetType.PAYMENT_CONFIRMATION, target_reference=_safe_reference(confirmation),
                        order=confirmation.order, installment=confirmation.installment, payment_attempt=confirmation.payment_attempt,
                        previous_state=previous_state_label, resulting_state=previous_status,
                        reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.REJECTED_INVALID_STATE,
                        request=request,
                    )
                    error_message = "Contact has no email address on file; cannot retry."
                else:
                    confirmation.recipient_snapshot = recipient
                    confirmation.status = PaymentConfirmation.Status.PENDING
                    confirmation.attempt_count = 0
                    confirmation.next_attempt_at = None
                    confirmation.last_failure_category = ""
                    confirmation.save(update_fields=[
                        "recipient_snapshot", "status", "attempt_count", "next_attempt_at", "last_failure_category", "updated_at",
                    ])

                    AdminAuditService().record(
                        administrator=administrator, action_type=AdminAuditLog.ActionType.RETRY_CONFIRMATION,
                        target_type=AdminAuditLog.TargetType.PAYMENT_CONFIRMATION, target_reference=_safe_reference(confirmation),
                        order=confirmation.order, installment=confirmation.installment, payment_attempt=confirmation.payment_attempt,
                        previous_state=previous_state_label, resulting_state=confirmation.status,
                        reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.SUCCESS,
                        request=request,
                    )
                    log_service_success(logger, "PaymentConfirmationAdministrationService", "retry_confirmation", confirmation_id=confirmation.id)

        if error_message is not None:
            raise AdminActionError(error_message)
        return confirmation


class EnrollmentAdministrationService:
    """
    Mutates live ClickFunnels access state (freeze/resume a customer's course
    enrollment). The core operations — freeze_enrollment_attempt /
    resume_enrollment_attempt — work on ANY successful EnrollmentAttempt,
    whether it originated from a checkout Order or a manual/bulk enroll via
    apps.enrollments (which has always been decoupled from apps.payments).
    Suspension state therefore lives on EnrollmentAttempt itself, not on
    anything Order-shaped — an Order may not even exist.

    freeze_order_enrollment/resume_order_enrollment are thin Order-centric
    wrappers used by the operations hub: they resolve the Order's (contact,
    course) down to the matching EnrollmentAttempt, delegate to the core
    method for the actual ClickFunnels call + attempt-level audit row, then
    additionally mirror Order.status to SUSPENDED/ACTIVE as a secondary,
    best-effort signal (its own separate audit row, since it's a distinct
    fact from "is ClickFunnels access suspended").

    Unlike every other method in this file, these call an external provider
    — so, unlike cancel_order/cancel_installment/waive_installment (which
    safely lock-then-mutate purely locally), the ClickFunnels call happens
    OUTSIDE any transaction.atomic() block, mirroring
    PaymentAttemptAdministrationService.check_tara_status's shape with Tara:
    plain unlocked read -> external call with no lock held -> re-lock and
    apply the local mutation only after the provider has responded.
    """

    def freeze_enrollment_attempt(self, enrollment_attempt_id: int, reason: str, administrator, request=None) -> EnrollmentAttempt:
        return self._set_attempt_suspension(
            enrollment_attempt_id, suspended=True, reason=reason, administrator=administrator, request=request,
            action_type=AdminAuditLog.ActionType.FREEZE_ENROLLMENT,
        )

    def resume_enrollment_attempt(self, enrollment_attempt_id: int, reason: str, administrator, request=None) -> EnrollmentAttempt:
        return self._set_attempt_suspension(
            enrollment_attempt_id, suspended=False, reason=reason, administrator=administrator, request=request,
            action_type=AdminAuditLog.ActionType.RESUME_ENROLLMENT,
        )

    def _set_attempt_suspension(
        self, enrollment_attempt_id: int, suspended: bool, reason: str, administrator, request, action_type: str,
    ) -> EnrollmentAttempt:
        log_service_start(
            logger, "EnrollmentAdministrationService", "_set_attempt_suspension",
            enrollment_attempt_id=enrollment_attempt_id, suspended=suspended,
        )
        attempt = EnrollmentAttempt.objects.select_related("contact", "course").get(pk=enrollment_attempt_id)
        target_reference = f"EnrollmentAttempt#{attempt.pk}"
        state_label = lambda s: "SUSPENDED" if s else "ACTIVE"  # noqa: E731

        if attempt.status != EnrollmentAttempt.Status.SUCCESS or not attempt.cf_enrollment_id:
            AdminAuditService().record(
                administrator=administrator, action_type=action_type,
                target_type=AdminAuditLog.TargetType.ENROLLMENT_ATTEMPT, target_reference=target_reference,
                previous_state=attempt.status, resulting_state=attempt.status,
                reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.REJECTED_INVALID_STATE,
                request=request,
            )
            raise AdminActionError("This enrollment has no successful ClickFunnels record on file; nothing to freeze/resume.")

        if attempt.cf_suspended == suspended:
            AdminAuditService().record(
                administrator=administrator, action_type=action_type,
                target_type=AdminAuditLog.TargetType.ENROLLMENT_ATTEMPT, target_reference=target_reference,
                previous_state=state_label(suspended), resulting_state=state_label(suspended),
                reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.NO_OP_ALREADY_IN_STATE,
                request=request,
            )
            return attempt

        bundle = self._build_enrollment_service()
        if bundle is None:
            AdminAuditService().record(
                administrator=administrator, action_type=action_type,
                target_type=AdminAuditLog.TargetType.ENROLLMENT_ATTEMPT, target_reference=target_reference,
                previous_state=state_label(attempt.cf_suspended), resulting_state=state_label(attempt.cf_suspended),
                reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.FAILED,
                request=request,
            )
            raise AdminActionError("No active ClickFunnels configuration/workspace available.")
        enrollment_service, config = bundle

        try:
            enrollment_service.set_enrollment_suspension(
                workspace_subdomain=config.workspace_subdomain,
                cf_enrollment_id=attempt.cf_enrollment_id, suspended=suspended, reason=reason,
            )
        except Exception as e:
            log_service_failure(
                logger, "EnrollmentAdministrationService", "_set_attempt_suspension", e,
                enrollment_attempt_id=enrollment_attempt_id,
            )
            AdminAuditService().record(
                administrator=administrator, action_type=action_type,
                target_type=AdminAuditLog.TargetType.ENROLLMENT_ATTEMPT, target_reference=target_reference,
                previous_state=state_label(attempt.cf_suspended), resulting_state=state_label(attempt.cf_suspended),
                reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.FAILED,
                request=request,
            )
            raise AdminActionError("Could not update the ClickFunnels enrollment; no local state was changed.") from e

        # Provider call succeeded — apply local state atomically, re-locking
        # in case of a race during the network call above.
        with transaction.atomic():
            locked = EnrollmentAttempt.objects.select_for_update().get(pk=attempt.pk)
            locked.cf_suspended = suspended
            locked.cf_suspended_at = timezone.now() if suspended else None
            locked.cf_suspension_reason = reason if suspended else ""
            locked.save(update_fields=["cf_suspended", "cf_suspended_at", "cf_suspension_reason", "updated_at"])

            AdminAuditService().record(
                administrator=administrator, action_type=action_type,
                target_type=AdminAuditLog.TargetType.ENROLLMENT_ATTEMPT, target_reference=target_reference,
                previous_state=state_label(not suspended), resulting_state=state_label(suspended),
                reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.SUCCESS,
                request=request,
            )
            log_service_success(
                logger, "EnrollmentAdministrationService", "_set_attempt_suspension",
                enrollment_attempt_id=locked.id,
            )

        return locked

    def freeze_order_enrollment(self, order_id: int, reason: str, administrator, request=None) -> Order:
        return self._set_order_suspension(order_id, suspended=True, reason=reason, administrator=administrator, request=request)

    def resume_order_enrollment(self, order_id: int, reason: str, administrator, request=None) -> Order:
        return self._set_order_suspension(order_id, suspended=False, reason=reason, administrator=administrator, request=request)

    def _set_order_suspension(self, order_id: int, suspended: bool, reason: str, administrator, request) -> Order:
        log_service_start(logger, "EnrollmentAdministrationService", "_set_order_suspension", order_id=order_id, suspended=suspended)
        action_type = AdminAuditLog.ActionType.FREEZE_ENROLLMENT if suspended else AdminAuditLog.ActionType.RESUME_ENROLLMENT

        order = Order.objects.select_related("customer", "course").get(pk=order_id)
        required_status = (Order.Status.ACTIVE, Order.Status.PAST_DUE) if suspended else (Order.Status.SUSPENDED,)
        if order.status not in required_status:
            AdminAuditService().record(
                administrator=administrator, action_type=action_type,
                target_type=AdminAuditLog.TargetType.ORDER, target_reference=_safe_reference(order),
                order=order, previous_state=order.status, resulting_state=order.status,
                reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.REJECTED_INVALID_STATE,
                request=request,
            )
            wanted = "ACTIVE or PAST_DUE" if suspended else "SUSPENDED"
            verb = "frozen" if suspended else "resumed"
            raise AdminActionError(f"Order status {order.status} is not eligible to be {verb} (must be {wanted}).")

        provisioning_request = (
            ProvisioningRequest.objects.filter(contact_id=order.customer_id, course_id=order.course_id)
            .exclude(status=ProvisioningRequest.Status.CANCELLED)
            .order_by("-created_at")
            .first()
        )
        attempt = (
            provisioning_request.attempts
            .filter(status=ProvisioningAttempt.Status.SUCCESS, enrollment_attempt__isnull=False)
            .exclude(enrollment_attempt__cf_enrollment_id="")
            .exclude(enrollment_attempt__cf_enrollment_id__isnull=True)
            .order_by("-created_at")
            .first()
            if provisioning_request else None
        )
        if attempt is None:
            AdminAuditService().record(
                administrator=administrator, action_type=action_type,
                target_type=AdminAuditLog.TargetType.ORDER, target_reference=_safe_reference(order),
                order=order, previous_state=order.status, resulting_state=order.status,
                reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.REJECTED_INVALID_STATE,
                request=request,
            )
            verb = "freeze" if suspended else "resume"
            raise AdminActionError(f"This order has no successful ClickFunnels enrollment on record; nothing to {verb}.")

        # Delegate the actual ClickFunnels call + attempt-level audit row to
        # the core method — propagates AdminActionError as-is on failure.
        if suspended:
            self.freeze_enrollment_attempt(attempt.enrollment_attempt_id, reason, administrator, request)
        else:
            self.resume_enrollment_attempt(attempt.enrollment_attempt_id, reason, administrator, request)

        # Mirror Order.status as a secondary, best-effort signal — its own
        # audit row, since "is CF suspended" and "is the order suspended"
        # are distinct facts recorded above and here respectively.
        with transaction.atomic():
            locked_order = Order.objects.select_for_update().get(pk=order.pk)
            previous_order_status = locked_order.status
            try:
                locked_order = OrderService().transition(
                    locked_order, Order.Status.SUSPENDED if suspended else Order.Status.ACTIVE,
                )
                resulting_status = locked_order.status
            except InvalidStateTransitionError:
                # ClickFunnels state already changed regardless (a provider
                # fact that can't be undone here) — e.g. the order was
                # concurrently cancelled/completed during the network call
                # above. Still SUCCESS for the mirror attempt; the mismatch
                # is visible in resulting_state for an operator to reconcile.
                resulting_status = f"{previous_order_status} (order transition skipped — no longer eligible)"

            AdminAuditService().record(
                administrator=administrator, action_type=action_type,
                target_type=AdminAuditLog.TargetType.ORDER, target_reference=_safe_reference(locked_order),
                order=locked_order, previous_state=previous_order_status, resulting_state=resulting_status,
                reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.SUCCESS,
                request=request,
            )
            log_service_success(logger, "EnrollmentAdministrationService", "_set_order_suspension", order_id=locked_order.id)

        return locked_order

    def _build_enrollment_service(self):
        """
        Mirrors ProvisioningService._build_enrollment_service() exactly
        (apps/provisioning/services.py) — duplicated rather than shared,
        consistent with how apps.provisioning.services and this module already
        resolve their existing circular dependency via function-local imports
        rather than a new shared helper module.
        """
        config = ConfigurationService().get_active_config()
        if not config or not config.workspace_id or not config.workspace_subdomain:
            return None
        client = ClickFunnelsClient.from_configuration(config)
        contact_service = ContactService(client=client)
        return EnrollmentService(client=client, contact_service=contact_service), config
