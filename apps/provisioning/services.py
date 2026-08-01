import time
from typing import Optional, Tuple

from django.db import IntegrityError, transaction
from django.utils import timezone

from shared.constants import (
    PROVISIONING_MAX_INLINE_RETRIES,
    PROVISIONING_INLINE_RETRY_BACKOFF_SECONDS,
    PROVISIONING_MAX_REQUEST_ATTEMPTS,
)
from shared.logging_utils import get_logger, log_service_start, log_service_success, log_service_failure
from apps.configuration.services import ConfigurationService
from apps.contacts.services import ContactService
from apps.enrollments.services import EnrollmentService
from apps.enrollments.models import EnrollmentAttempt
from apps.payments.models import Payment, PaymentTimelineEvent
from integrations.clickfunnels.client import ClickFunnelsClient
from integrations.clickfunnels.exceptions import ClickFunnelsAPIError, ClickFunnelsAuthError, ClickFunnelsRateLimitError

from .models import Product, ProvisioningAttempt, ProvisioningRequest

logger = get_logger(__name__)

# Non-retryable failure categories (Phase 7) — a request that fails for one of
# these reasons goes straight to MANUAL_REVIEW instead of the retryable FAILED
# state, since retrying without an operator fixing the underlying cause can
# never succeed.
_NON_RETRYABLE_CATEGORIES = {
    ProvisioningRequest.FailureCategory.MISSING_CONTACT_IDENTIFIER,
    ProvisioningRequest.FailureCategory.MISSING_COURSE_SNAPSHOT,
    ProvisioningRequest.FailureCategory.CONFIGURATION_ERROR,
    ProvisioningRequest.FailureCategory.AUTHORIZATION_ERROR,
    ProvisioningRequest.FailureCategory.MALFORMED_REQUEST,
}


class ProvisioningError(Exception):
    pass


_POLICY_TO_INITIAL_STATUS = {
    Product.ProvisioningPolicy.AUTOMATIC: ProvisioningRequest.Status.PENDING,
    Product.ProvisioningPolicy.MANUAL: ProvisioningRequest.Status.PENDING,
    Product.ProvisioningPolicy.SCHEDULED: ProvisioningRequest.Status.SCHEDULED,
    Product.ProvisioningPolicy.APPROVAL_REQUIRED: ProvisioningRequest.Status.AWAITING_APPROVAL,
}


class ProvisioningService:
    """
    Provisioning lifecycle only — separate from Payment's lifecycle by design.
    Never touches Tara; the only input is an already-MATCHED, HIGH-confidence
    Payment. See apps.payments for how a Payment gets there.
    """

    def create_request_from_payment(self, payment: Payment) -> Optional[ProvisioningRequest]:
        """
        Guarded entrypoint: refuses to run unless payment.status == MATCHED and
        payment.match_confidence == HIGH. Product.provisioning_policy == AUTOMATIC
        does NOT bypass this gate — a low-confidence match on an AUTOMATIC product
        still stops here, never silently auto-provisions on amount alone.

        Idempotent: if a (non-cancelled) ProvisioningRequest already exists for
        this payment, returns it instead of creating a second one — safe to call
        repeatedly, e.g. from a rematch after a configuration fix.
        """
        log_service_start(logger, "ProvisioningService", "create_request_from_payment", payment_id=payment.id)

        if payment.status != Payment.Status.MATCHED or payment.match_confidence != Payment.Confidence.HIGH:
            log_service_failure(
                logger, "ProvisioningService", "create_request_from_payment",
                ValueError("Payment is not a high-confidence match"), payment_id=payment.id,
            )
            return None

        existing = (
            ProvisioningRequest.objects.filter(payment=payment)
            .exclude(status=ProvisioningRequest.Status.CANCELLED)
            .first()
        )
        if existing:
            log_service_success(
                logger, "ProvisioningService", "create_request_from_payment",
                payment_id=payment.id, provisioning_request_id=existing.id,
            )
            return existing

        product = payment.matched_product
        contact = payment.matched_contact
        policy = product.provisioning_policy

        request = ProvisioningRequest.objects.create(
            payment=payment,
            product=product,
            contact=contact,
            policy_snapshot=policy,
            status=_POLICY_TO_INITIAL_STATUS[policy],
        )
        PaymentTimelineEvent.objects.create(
            payment=payment,
            event_type=PaymentTimelineEvent.EventType.PROVISIONING_REQUESTED,
            detail={"provisioning_request_id": request.id, "policy": policy},
        )
        log_service_success(
            logger, "ProvisioningService", "create_request_from_payment",
            payment_id=payment.id, provisioning_request_id=request.id, product_id=product.id,
        )

        if policy == Product.ProvisioningPolicy.AUTOMATIC:
            # execute() claims the request via a fresh select_for_update()
            # fetch (see _claim) and returns that (different) instance —
            # must use the return value, not the stale pre-execute() object.
            request = self.execute(request)

        return request

    def create_request_from_order(self, order) -> Optional[ProvisioningRequest]:
        """
        New-flow entry point (Phase 7, docs/TARA_INTEGRATION_PROJECT.md).
        Called only by PaymentCreditService.apply_verified_success(), inside
        its own atomic transaction, only once order.is_access_eligible has
        just become true from a newly-verified installment. Uses ONLY the
        Order's frozen snapshot (order.course, order.course_cf_id) — never
        the current PaymentPlan — so a plan edited after purchase can never
        change which course an existing order enrolls into.

        Idempotent per (contact, course) — a duplicate call (e.g. a later
        installment on a FULL_PAYMENT order re-checking eligibility, or a
        second Order for a course the contact already has a live request for)
        returns the existing request instead of creating a second one. This
        deliberately does NOT key on Order alone: the project brief prefers
        Contact+course uniqueness so multiple purchases never duplicate course
        membership, while still recording this Order for audit via the
        `order` field.

        Never calls execute() inline — Phase 7 requires ClickFunnels calls to
        happen only after the payment transaction that created this request
        has committed; a separate worker/management command
        (process_pending_provisioning) processes PENDING requests.
        """
        log_service_start(logger, "ProvisioningService", "create_request_from_order", order_id=order.id)

        existing = (
            ProvisioningRequest.objects.filter(contact_id=order.customer_id, course_id=order.course_id)
            .exclude(status=ProvisioningRequest.Status.CANCELLED)
            .first()
        )
        if existing:
            log_service_success(
                logger, "ProvisioningService", "create_request_from_order",
                order_id=order.id, provisioning_request_id=existing.id,
            )
            return existing

        try:
            with transaction.atomic():
                request = ProvisioningRequest.objects.create(
                    order=order,
                    course=order.course,
                    contact=order.customer,
                    policy_snapshot=Product.ProvisioningPolicy.AUTOMATIC,
                    status=ProvisioningRequest.Status.PENDING,
                )
        except IntegrityError:
            # Lost a race against a concurrent request for the same
            # (contact, course) originating from a different Order — the DB
            # constraint is authoritative; fetch and return the winner.
            request = ProvisioningRequest.objects.filter(
                contact_id=order.customer_id, course_id=order.course_id,
            ).exclude(status=ProvisioningRequest.Status.CANCELLED).first()
            if request is None:
                raise
        log_service_success(
            logger, "ProvisioningService", "create_request_from_order",
            order_id=order.id, provisioning_request_id=request.id,
        )
        return request

    def approve(self, request: ProvisioningRequest, user) -> ProvisioningRequest:
        if request.status != ProvisioningRequest.Status.AWAITING_APPROVAL:
            raise ProvisioningError("Only AWAITING_APPROVAL requests can be approved.")
        request.approved_by = user
        request.approved_at = timezone.now()
        request.status = ProvisioningRequest.Status.PENDING
        request.save(update_fields=["approved_by", "approved_at", "status", "updated_at"])
        log_service_success(
            logger, "ProvisioningService", "approve", provisioning_request_id=request.id,
        )
        return request

    def cancel(self, request: ProvisioningRequest) -> ProvisioningRequest:
        request.status = ProvisioningRequest.Status.CANCELLED
        request.save(update_fields=["status", "updated_at"])
        log_service_success(logger, "ProvisioningService", "cancel", provisioning_request_id=request.id)
        return request

    def execute(self, request: ProvisioningRequest) -> ProvisioningRequest:
        """
        Calls the existing EnrollmentService once per course — the Product's
        courses (legacy origin) or the Order's single frozen course (new
        origin, Phase 7) — skipping any course that already has a SUCCESS
        ProvisioningAttempt (so a retry after a partial failure never
        double-enrolls). All-succeed -> COMPLETED. Any failure -> FAILED
        (retryable — an explicit Admin action, the nightly SCHEDULED batch, or
        process_pending_provisioning may retry it) unless the failure is one
        of the non-retryable categories (_NON_RETRYABLE_CATEGORIES), in which
        case -> MANUAL_REVIEW immediately, since automatic/human retry without
        first fixing the underlying cause can never succeed.

        Concurrency-safe: the actual claim (PENDING/FAILED -> IN_PROGRESS,
        attempt_count++) happens in `_claim`, a short separate locked
        transaction, so two workers racing to process the same request only
        ever have one of them proceed past this point.
        """
        claimed = self._claim(request.pk)
        if claimed is None:
            request.refresh_from_db()
            log_service_success(
                logger, "ProvisioningService", "execute",
                provisioning_request_id=request.pk, note_not_claimed=True,
            )
            return request
        request = claimed
        log_service_start(logger, "ProvisioningService", "execute", provisioning_request_id=request.id)

        is_new_flow = request.order_id is not None

        if not request.contact.email:
            self._finish_non_retryable(
                request, "Contact has no email address on file.",
                ProvisioningRequest.FailureCategory.MISSING_CONTACT_IDENTIFIER,
            )
            return request

        if is_new_flow and not request.order.course_cf_id:
            self._finish_non_retryable(
                request, "Order has no course_cf_id snapshot.",
                ProvisioningRequest.FailureCategory.MISSING_COURSE_SNAPSHOT,
            )
            return request

        bundle = self._build_enrollment_service()
        if not bundle:
            self._finish_non_retryable(
                request, "No active ClickFunnels configuration/workspace available.",
                ProvisioningRequest.FailureCategory.CONFIGURATION_ERROR,
            )
            return request

        enrollment_service, config = bundle
        already_succeeded_course_ids = set(
            request.attempts.filter(status=ProvisioningAttempt.Status.SUCCESS).values_list("course_id", flat=True)
        )

        courses = list(request.product.courses.all()) if request.product_id else [request.course]

        all_succeeded = True
        any_non_retryable = False
        last_error = ""
        for course in courses:
            if course.id in already_succeeded_course_ids:
                continue

            cf_course_id = course.cf_course_id if request.product_id else request.order.course_cf_id
            attempt_status, enrollment_attempt_id, error, category = self._enroll_with_retry(
                enrollment_service, config, request.contact, course, cf_course_id,
            )
            ProvisioningAttempt.objects.create(
                provisioning_request=request,
                course=course,
                enrollment_attempt_id=enrollment_attempt_id,
                status=attempt_status,
            )
            if attempt_status != ProvisioningAttempt.Status.SUCCESS:
                all_succeeded = False
                last_error = error
                if category in _NON_RETRYABLE_CATEGORIES:
                    any_non_retryable = True
                    request.failure_category = category

        if all_succeeded:
            request.status = ProvisioningRequest.Status.COMPLETED
            request.last_error = ""
            request.failure_category = ""
            request.save(update_fields=["status", "last_error", "failure_category", "updated_at"])
            self._record_success(request)
        elif any_non_retryable:
            request.status = ProvisioningRequest.Status.MANUAL_REVIEW
            request.last_error = last_error
            request.save(update_fields=["status", "last_error", "failure_category", "updated_at"])
            self._record_failure(request, last_error)
        else:
            request.status = ProvisioningRequest.Status.FAILED
            request.last_error = last_error
            request.failure_category = ProvisioningRequest.FailureCategory.TRANSIENT_PROVIDER_ERROR
            request.save(update_fields=["status", "last_error", "failure_category", "updated_at"])
            self._record_failure(request, last_error)

        log_service_success(
            logger, "ProvisioningService", "execute", provisioning_request_id=request.id,
        )
        return request

    _CLAIMABLE_STATUSES = (
        ProvisioningRequest.Status.PENDING,
        ProvisioningRequest.Status.SCHEDULED,
        ProvisioningRequest.Status.FAILED,
    )

    def _claim(self, request_id: int) -> Optional[ProvisioningRequest]:
        """
        Atomically claims a PENDING, SCHEDULED, or FAILED request for
        processing — the concurrency-safe "lock/atomically claim" step
        required by Phase 7 for the new flow, applied uniformly to the legacy
        flow too (it shares this same execute() method; SCHEDULED is how the
        nightly batch command reaches it). Returns None if the request was
        already claimed/completed/cancelled/under manual review, or if
        bounded attempts have been exhausted (in which case it's moved to
        MANUAL_REVIEW here so it stops being retried automatically).
        """
        with transaction.atomic():
            try:
                request = ProvisioningRequest.objects.select_for_update().get(pk=request_id)
            except ProvisioningRequest.DoesNotExist:
                return None
            if request.status not in self._CLAIMABLE_STATUSES:
                return None
            if request.attempt_count >= PROVISIONING_MAX_REQUEST_ATTEMPTS:
                request.status = ProvisioningRequest.Status.MANUAL_REVIEW
                request.last_error = "Maximum provisioning attempts exceeded."
                request.failure_category = ProvisioningRequest.FailureCategory.MAX_ATTEMPTS_EXCEEDED
                request.save(update_fields=["status", "last_error", "failure_category", "updated_at"])
                return None
            request.status = ProvisioningRequest.Status.IN_PROGRESS
            request.attempt_count += 1
            request.save(update_fields=["status", "attempt_count", "updated_at"])
            return request

    def _finish_non_retryable(self, request: ProvisioningRequest, error: str, category: str) -> None:
        request.status = ProvisioningRequest.Status.MANUAL_REVIEW
        request.last_error = error
        request.failure_category = category
        request.save(update_fields=["status", "last_error", "failure_category", "updated_at"])
        self._record_failure(request, error)

    def _build_enrollment_service(self) -> Optional[Tuple[EnrollmentService, object]]:
        config_service = ConfigurationService()
        config = config_service.get_active_config()
        if not config or not config.workspace_id or not config.workspace_subdomain:
            return None
        client = ClickFunnelsClient.from_configuration(config)
        contact_service = ContactService(client=client)
        return EnrollmentService(client=client, contact_service=contact_service), config

    def _enroll_with_retry(self, enrollment_service, config, contact, course, cf_course_id):
        """
        Returns (ProvisioningAttempt.Status, enrollment_attempt_id, error, failure_category).
        failure_category is only meaningful when status != SUCCESS.

        Enrollment endpoint has no known idempotency check (no "list
        enrollments"/"is contact enrolled" call exists on ClickFunnelsClient —
        see integrations/clickfunnels/client.py). If a prior call actually
        succeeded at the provider but its response was lost (timeout), a
        retry here calls enroll_contact_in_course again; this is a documented,
        unresolved ambiguity (Known/Decision-required, see Phase Result
        report) — the safest existing behavior available is relied on: our
        own ProvisioningAttempt SUCCESS records (checked by the caller before
        this is invoked) are what prevent a *repeat* execute() call from
        re-enrolling a course that already recorded success locally.
        """
        attempts = 0
        last_error = ""
        while True:
            try:
                result = enrollment_service.enroll_contact(
                    workspace_subdomain=config.workspace_subdomain,
                    workspace_id=int(config.workspace_id),
                    email=contact.email,
                    cf_course_id=cf_course_id,
                )
                if result.status == EnrollmentAttempt.Status.SUCCESS:
                    return ProvisioningAttempt.Status.SUCCESS, result.enrollment_attempt_id, "", ""
                # A normal (non-exception) FAILURE result — EnrollmentService
                # already tried and recorded it. No specific non-retryable
                # signal here, so treat as a generic transient failure —
                # retryable, same as the pre-Phase-7 behavior.
                return (
                    ProvisioningAttempt.Status.FAILURE, result.enrollment_attempt_id,
                    result.error_message or "Enrollment failed",
                    ProvisioningRequest.FailureCategory.TRANSIENT_PROVIDER_ERROR,
                )
            except ClickFunnelsAuthError as e:
                return ProvisioningAttempt.Status.FAILURE, None, str(e), ProvisioningRequest.FailureCategory.AUTHORIZATION_ERROR
            except ClickFunnelsRateLimitError as e:
                last_error = str(e)
            except ClickFunnelsAPIError as e:
                status_code = getattr(e, "status_code", None)
                if not (status_code and 500 <= status_code < 600):
                    # e.g. 404/422 — not transient, retrying won't help
                    return (
                        ProvisioningAttempt.Status.FAILURE, None, str(e),
                        ProvisioningRequest.FailureCategory.MALFORMED_REQUEST,
                    )
                last_error = str(e)
            except Exception as e:
                return ProvisioningAttempt.Status.FAILURE, None, str(e), ProvisioningRequest.FailureCategory.CONFIGURATION_ERROR

            if attempts >= PROVISIONING_MAX_INLINE_RETRIES:
                return (
                    ProvisioningAttempt.Status.FAILURE, None, last_error,
                    ProvisioningRequest.FailureCategory.TRANSIENT_PROVIDER_ERROR,
                )
            time.sleep(PROVISIONING_INLINE_RETRY_BACKOFF_SECONDS[attempts])
            attempts += 1

    def _record_success(self, request: ProvisioningRequest):
        if request.payment_id is None:
            return  # new flow (Phase 7) has no legacy Payment to attach a timeline event to
        PaymentTimelineEvent.objects.create(
            payment=request.payment,
            event_type=PaymentTimelineEvent.EventType.PROVISIONING_COMPLETED,
            detail={"provisioning_request_id": request.id},
        )

    def _record_failure(self, request: ProvisioningRequest, error: str):
        if request.payment_id is None:
            return  # new flow (Phase 7) has no legacy Payment to attach a timeline event to
        PaymentTimelineEvent.objects.create(
            payment=request.payment,
            event_type=PaymentTimelineEvent.EventType.PROVISIONING_FAILED,
            detail={"provisioning_request_id": request.id, "error": error},
        )

    def retry_request(self, request_id: int, reason: str, administrator, request_context=None) -> ProvisioningRequest:
        """
        Administrative retry (Phase 8, docs/TARA_INTEGRATION_PROJECT.md) —
        hardens the Phase 7 ProvisioningRequestAdmin.retry_action, which used
        to call execute() (and therefore ClickFunnels) synchronously inside
        the admin request. This method only resets safe state and writes an
        audit record — it never calls execute()/ClickFunnels itself; the
        request is left PENDING for the existing worker
        (process_pending_provisioning) to claim afterward, out of this
        transaction. COMPLETED requests can never be retried. For the new
        (Order/course) flow, eligibility is revalidated — a request whose
        Order is no longer access-eligible is rejected rather than blindly
        reset (defense-in-depth; this should not normally happen since
        eligibility, once reached, is never revoked by anything in this
        codebase). attempt_count is reset to 0 so the retry is actually
        claimable against the bounded-attempts check in _claim(); the prior
        attempt_count/failure_category are preserved in the AdminAuditLog
        entry's previous_state rather than on the live row.
        """
        from apps.payments.admin_services import AdminAuditService
        from apps.payments.models import AdminAuditLog

        log_service_start(logger, "ProvisioningService", "retry_request", provisioning_request_id=request_id)
        error_message = None
        with transaction.atomic():
            req = ProvisioningRequest.objects.select_for_update().get(pk=request_id)
            previous_status = req.status
            previous_state_label = f"{previous_status} (attempts={req.attempt_count}, category={req.failure_category or 'n/a'})"
            target_reference = f"ProvisioningRequest#{req.pk}"

            if req.status == ProvisioningRequest.Status.COMPLETED:
                AdminAuditService().record(
                    administrator=administrator, action_type=AdminAuditLog.ActionType.RETRY_PROVISIONING,
                    target_type=AdminAuditLog.TargetType.PROVISIONING_REQUEST, target_reference=target_reference,
                    order=req.order, previous_state=previous_state_label, resulting_state=previous_status,
                    reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.REJECTED_INVALID_STATE,
                    request=request_context,
                )
                error_message = "A completed provisioning request cannot be retried."
            elif req.status not in (ProvisioningRequest.Status.FAILED, ProvisioningRequest.Status.MANUAL_REVIEW):
                AdminAuditService().record(
                    administrator=administrator, action_type=AdminAuditLog.ActionType.RETRY_PROVISIONING,
                    target_type=AdminAuditLog.TargetType.PROVISIONING_REQUEST, target_reference=target_reference,
                    order=req.order, previous_state=previous_state_label, resulting_state=previous_status,
                    reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.REJECTED_INVALID_STATE,
                    request=request_context,
                )
                error_message = "Only FAILED or MANUAL_REVIEW requests can be retried."
            elif req.order_id is not None and not req.order.is_access_eligible:
                AdminAuditService().record(
                    administrator=administrator, action_type=AdminAuditLog.ActionType.RETRY_PROVISIONING,
                    target_type=AdminAuditLog.TargetType.PROVISIONING_REQUEST, target_reference=target_reference,
                    order=req.order, previous_state=previous_state_label, resulting_state=previous_status,
                    reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.REJECTED_INVALID_STATE,
                    request=request_context,
                )
                error_message = "The order is no longer access-eligible; cannot retry enrollment."
            else:
                req.status = ProvisioningRequest.Status.PENDING
                req.attempt_count = 0
                req.save(update_fields=["status", "attempt_count", "updated_at"])

                AdminAuditService().record(
                    administrator=administrator, action_type=AdminAuditLog.ActionType.RETRY_PROVISIONING,
                    target_type=AdminAuditLog.TargetType.PROVISIONING_REQUEST, target_reference=target_reference,
                    order=req.order, previous_state=previous_state_label, resulting_state=req.status,
                    reason=reason, outcome_category=AdminAuditLog.OutcomeCategory.SUCCESS,
                    request=request_context,
                )
                log_service_success(logger, "ProvisioningService", "retry_request", provisioning_request_id=req.id)

        if error_message is not None:
            raise ProvisioningError(error_message)
        return req

    def process_pending(self, limit: int = 100) -> int:
        """
        Reusable entry point for a management command / future scheduler
        (Phase 9) to process durable enrollment work — both flows. Only picks
        up PENDING/FAILED requests (never MANUAL_REVIEW, which requires an
        operator). Returns the number of requests processed.
        """
        request_ids = list(
            ProvisioningRequest.objects.filter(
                status__in=[ProvisioningRequest.Status.PENDING, ProvisioningRequest.Status.FAILED],
            ).order_by("created_at").values_list("id", flat=True)[:limit]
        )
        processed = 0
        for request_id in request_ids:
            request = ProvisioningRequest.objects.get(pk=request_id)
            self.execute(request)
            processed += 1
        return processed
