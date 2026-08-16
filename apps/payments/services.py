import hashlib
import hmac as hmac_module
import json
import re
import smtplib
from datetime import date, timedelta
from decimal import Decimal
from typing import Callable, Optional, Tuple
from urllib.parse import urlsplit

from cryptography.fernet import InvalidToken
from django.conf import settings
from django.core import signing
from django.core.mail import send_mail
from django.db import IntegrityError, models, transaction
from django.template.loader import render_to_string
from django.utils import timezone
from pydantic import ValidationError as PydanticValidationError

from shared.constants import (
    PAYMENT_CONFIRMATION_MAX_ATTEMPTS,
    PAYMENT_CONFIRMATION_RETRY_BACKOFF_MINUTES,
    TARA_WEBHOOK_MAX_BODY_BYTES,
)
from shared.logging_utils import get_logger, log_service_start, log_service_success, log_service_failure
from shared.security import encrypt_value
from apps.contacts.models import Contact
from integrations.payments.tara.client import TaraClient
from integrations.payments.tara.exceptions import (
    TaraClientError,
    TaraConfigurationError,
    TaraConnectionError,
    TaraCredentialError,
    TaraInvalidRequestError,
    TaraMalformedResponseError,
    TaraProviderBusinessError,
    TaraServerError,
    TaraTimeoutError,
)
from integrations.payments.tara.schemas import TaraTransactionStatus, TaraWebhookPayload

from .models import (
    Installment,
    Order,
    PaymentAttempt,
    PaymentConfirmation,
    PaymentPlan,
    TaraConfig,
    TaraWebhookEvent,
)

logger = get_logger(__name__)


class OrderCreationError(Exception):
    pass


class InvalidStateTransitionError(Exception):
    pass


class PaymentCreditError(Exception):
    pass


class PaymentIdConflictError(PaymentCreditError):
    pass


class WebhookRejectedError(Exception):
    """Raised only for structurally invalid webhook bodies (malformed JSON, non-object, oversized) — nothing was or could be durably recorded."""
    pass


class CheckoutError(Exception):
    """Base for all customer-facing checkout failures. str(exception) must always be safe to show to a customer."""
    pass


class CheckoutConfigurationError(CheckoutError):
    """The application itself isn't ready for checkout (e.g. PUBLIC_BASE_URL unset) — not the customer's fault."""
    pass


def normalize_phone(phone: str) -> str:
    """
    Strips common formatting so phone comparisons aren't defeated by spacing/
    punctuation. The exact Tara phone format is TBD pending contract capture
    (docs/TARA_API_CONTRACT.md); kept isolated here so the normalization rule can
    change without touching matching logic.
    """
    return re.sub(r"[^\d+]", "", phone or "")


class TaraConfigService:
    """
    Credential write path only. Deliberately has no verify_token()/validation
    call — docs/Tara_API_Reference_Technique.docx documents no safe, read-only
    endpoint to check credentials against (Phase 0 discovery, decision D8), and
    the old verify_token() called a fabricated `GET {BASE_URL}/account` that
    does not exist in Tara's documented API surface. Every TaraConfig therefore
    stays validation_status=PENDING until a later phase confirms a real check.
    """

    def update_credentials(self, config: TaraConfig, api_key: Optional[str], webhook_secret: Optional[str]):
        """
        Encrypts new credentials onto `config` in memory — does not save.
        Persistence is the caller's responsibility (see
        apps/payments/admin.py::TaraConfigForm.save(), which respects Django's
        ModelForm commit=False contract). A blank/falsy value leaves the
        existing encrypted field untouched; only ever called with a non-empty
        new value from the admin, since TaraConfigForm excludes these fields
        from Meta.fields precisely so a blank submission never reaches here as
        an accidental overwrite.
        """
        if api_key:
            config.api_key = encrypt_value(api_key.strip())
        if webhook_secret:
            config.webhook_secret = encrypt_value(webhook_secret.strip())

    def get_active_config(self) -> Optional[TaraConfig]:
        return TaraConfig.objects.filter(is_active=True).first()

    def get_client(self) -> TaraClient:
        """
        Obtains the active TaraConfig server-side and constructs a TaraClient
        from it — the sanctioned way for apps/ code to get a usable client
        without ever handling raw/decrypted credentials itself (Phase 4,
        docs/TARA_INTEGRATION_PROJECT.md).

        Fails safely and distinctly:
        - TaraConfigurationError — no active configuration, or the active one
          is missing businessId (an incomplete configuration, not a secret
          problem).
        - TaraCredentialError — the stored api_key/webhook_secret could not be
          decrypted (wrong/rotated FIELD_ENCRYPTION_KEY, corrupt ciphertext).

        Never returns a partially-decrypted client and never includes any
        ciphertext or key material in either exception's message.
        """
        config = self.get_active_config()
        if config is None:
            raise TaraConfigurationError("No active Tara configuration.")
        if not config.business_id:
            raise TaraConfigurationError("The active Tara configuration is missing businessId.")

        try:
            return TaraClient.from_configuration(config)
        except (ValueError, InvalidToken) as e:
            raise TaraCredentialError("Could not decrypt the stored Tara credentials.") from e


_ORDER_TRANSITIONS = {
    Order.Status.PENDING: {Order.Status.ACTIVE, Order.Status.CANCELLED, Order.Status.SUSPENDED},
    Order.Status.ACTIVE: {Order.Status.PAST_DUE, Order.Status.COMPLETED, Order.Status.CANCELLED, Order.Status.SUSPENDED},
    Order.Status.PAST_DUE: {Order.Status.ACTIVE, Order.Status.COMPLETED, Order.Status.CANCELLED, Order.Status.SUSPENDED},
    Order.Status.COMPLETED: set(),  # terminal
    Order.Status.CANCELLED: set(),  # terminal
    Order.Status.SUSPENDED: {Order.Status.ACTIVE, Order.Status.CANCELLED},
}

_INSTALLMENT_TRANSITIONS = {
    Installment.Status.SCHEDULED: {
        Installment.Status.DUE, Installment.Status.PAID, Installment.Status.FAILED,
        Installment.Status.WAIVED, Installment.Status.CANCELLED,
    },
    Installment.Status.DUE: {
        Installment.Status.PENDING, Installment.Status.PAID, Installment.Status.FAILED,
        Installment.Status.WAIVED, Installment.Status.CANCELLED,
    },
    Installment.Status.PENDING: {
        Installment.Status.PAID, Installment.Status.FAILED,
        Installment.Status.WAIVED, Installment.Status.CANCELLED,
    },
    Installment.Status.PAID: set(),  # terminal — a verified payment can never become cancelled/failed/waived
    Installment.Status.FAILED: {
        Installment.Status.DUE, Installment.Status.PENDING,
        Installment.Status.WAIVED, Installment.Status.CANCELLED,
    },
    Installment.Status.WAIVED: set(),  # terminal, operator decision
    Installment.Status.CANCELLED: set(),  # terminal
}

_PAYMENT_ATTEMPT_TRANSITIONS = {
    PaymentAttempt.Status.CREATED: {
        PaymentAttempt.Status.LINK_CREATED, PaymentAttempt.Status.FAILED,
        PaymentAttempt.Status.EXPIRED, PaymentAttempt.Status.UNKNOWN,
    },
    PaymentAttempt.Status.LINK_CREATED: {
        PaymentAttempt.Status.PENDING, PaymentAttempt.Status.SUCCEEDED, PaymentAttempt.Status.FAILED,
        PaymentAttempt.Status.EXPIRED, PaymentAttempt.Status.UNKNOWN,
    },
    PaymentAttempt.Status.PENDING: {
        PaymentAttempt.Status.SUCCEEDED, PaymentAttempt.Status.FAILED,
        PaymentAttempt.Status.EXPIRED, PaymentAttempt.Status.UNKNOWN,
    },
    PaymentAttempt.Status.SUCCEEDED: set(),  # terminal — a provider-confirmed payment can never change
    PaymentAttempt.Status.FAILED: {PaymentAttempt.Status.UNKNOWN},  # reconciliation may re-check
    PaymentAttempt.Status.EXPIRED: {PaymentAttempt.Status.UNKNOWN},
    PaymentAttempt.Status.UNKNOWN: {
        PaymentAttempt.Status.SUCCEEDED, PaymentAttempt.Status.FAILED, PaymentAttempt.Status.EXPIRED,
    },
}


class OrderService:
    """
    Order/Installment persistence only (docs/TARA_INTEGRATION_PROJECT.md Phase 3).
    Never calls Tara, never creates a PaymentAttempt (see PaymentAttempt's
    docstring — that's Phase 5's job), never touches ClickFunnels.
    """

    def create_order(
        self, customer: Contact, plan_id: int, idempotency_key: str, start_date: Optional[date] = None,
    ) -> Tuple[Order, bool]:
        """
        Idempotent on idempotency_key. Returns (order, created) — created=False
        means an existing order for this exact key/customer/plan was returned
        unchanged, safe to call repeatedly (e.g. a retried checkout POST).
        Raises OrderCreationError if the key was already used with a different
        customer or plan — never silently substituted.

        Always re-loads the PaymentPlan from the database by id; never trusts
        a plan instance the caller might already be holding, since Phase 3's
        eventual caller (Phase 5 checkout) must never treat browser-submitted
        plan/price/course data as authoritative.
        """
        log_service_start(logger, "OrderService", "create_order")
        start_date = start_date or timezone.now().date()

        existing = Order.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            self._check_idempotency_conflict(existing, customer, plan_id)
            log_service_success(logger, "OrderService", "create_order", order_id=existing.id)
            return existing, False

        try:
            with transaction.atomic():
                plan = PaymentPlan.objects.select_for_update().filter(pk=plan_id).first()
                if plan is None:
                    raise OrderCreationError(f"Payment plan {plan_id} does not exist.")
                if not plan.is_active:
                    raise OrderCreationError(f"Payment plan {plan_id} is not active.")

                # plan.course is PROTECTed, so it structurally cannot be missing
                # while the plan row exists — this check is defense in depth,
                # not the primary guarantee.
                course = plan.course
                if course is None:
                    raise OrderCreationError(f"Payment plan {plan_id} has no usable course.")

                order = Order.objects.create(
                    customer=customer,
                    plan=plan,
                    course=course,
                    idempotency_key=idempotency_key,
                    status=Order.Status.PENDING,
                    plan_code=plan.code,
                    plan_name=plan.name,
                    course_cf_id=course.cf_course_id,
                    course_name=course.name,
                    currency=plan.currency,
                    installment_count=plan.installment_count,
                    installment_amount=plan.installment_amount,
                    installment_interval_days=plan.installment_interval_days,
                    total_expected_amount=plan.computed_total,
                    access_policy=plan.access_policy,
                )
                self._create_installments(order, start_date)
        except IntegrityError:
            # A concurrent caller won the race on idempotency_key's unique
            # constraint between our SELECT above and our INSERT — the whole
            # atomic block (order + installments) rolled back, so there is no
            # partial state to clean up. Fetch what the winner created.
            existing = Order.objects.filter(idempotency_key=idempotency_key).first()
            if existing is None:
                raise
            self._check_idempotency_conflict(existing, customer, plan_id)
            log_service_success(logger, "OrderService", "create_order", order_id=existing.id)
            return existing, False

        log_service_success(logger, "OrderService", "create_order", order_id=order.id)
        return order, True

    def _check_idempotency_conflict(self, existing: Order, customer: Contact, plan_id: int):
        if existing.customer_id != customer.id or existing.plan_id != plan_id:
            raise OrderCreationError(
                f"Idempotency key {existing.idempotency_key!r} was already used with a different customer or plan."
            )

    def _create_installments(self, order: Order, start_date: date):
        """
        Deterministic from the order's own frozen snapshot — installment 1 is
        due at start_date; each subsequent installment is due
        installment_interval_days later than the previous one. No floating-
        point arithmetic: amounts are copied Decimal-to-Decimal, due dates use
        integer-day timedelta arithmetic.
        """
        installments = [
            Installment(
                order=order,
                sequence=sequence,
                expected_amount=order.installment_amount,
                currency=order.currency,
                due_date=start_date + timedelta(days=order.installment_interval_days * (sequence - 1)),
                status=Installment.Status.SCHEDULED,
            )
            for sequence in range(1, order.installment_count + 1)
        ]
        Installment.objects.bulk_create(installments)

    def transition(self, order: Order, new_status: str) -> Order:
        """The only sanctioned way to change Order.status. Idempotent; rejects transitions not in _ORDER_TRANSITIONS."""
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order.pk)
            if new_status == locked.status:
                return locked
            allowed = _ORDER_TRANSITIONS.get(locked.status, set())
            if new_status not in allowed:
                raise InvalidStateTransitionError(f"Order {locked.id}: cannot transition {locked.status} -> {new_status}.")
            locked.status = new_status
            locked.save(update_fields=["status", "updated_at"])
            log_service_success(logger, "OrderService", "transition", order_id=locked.id)
            return locked

    def cancel_order(self, order: Order) -> Order:
        """
        Cancels the order without touching any Installment/PaymentAttempt —
        cancellation must never rewrite payment history. Only Order.status
        changes; already-PAID installments and SUCCEEDED attempts are
        untouched, and _INSTALLMENT_TRANSITIONS/_PAYMENT_ATTEMPT_TRANSITIONS
        make PAID/SUCCEEDED terminal regardless, so nothing downstream could
        rewrite them even if something tried.
        """
        return self.transition(order, Order.Status.CANCELLED)


class InstallmentService:
    def transition(self, installment: Installment, new_status: str, paid_amount: Optional[Decimal] = None) -> Installment:
        """The only sanctioned way to change Installment.status. Idempotent; rejects transitions not in _INSTALLMENT_TRANSITIONS."""
        with transaction.atomic():
            locked = Installment.objects.select_for_update().get(pk=installment.pk)
            if new_status == locked.status:
                return locked
            allowed = _INSTALLMENT_TRANSITIONS.get(locked.status, set())
            if new_status not in allowed:
                raise InvalidStateTransitionError(
                    f"Installment {locked.id}: cannot transition {locked.status} -> {new_status}."
                )
            locked.status = new_status
            if new_status == Installment.Status.PAID:
                locked.paid_amount = paid_amount if paid_amount is not None else locked.expected_amount
                locked.paid_at = timezone.now()
            locked.save()
            log_service_success(logger, "InstallmentService", "transition", installment_id=locked.id)
            return locked


class PaymentAttemptService:
    """
    Deliberately not called anywhere else in Phase 3 — see PaymentAttempt's
    model docstring. Exists now, tested now, so Phase 5 has a single
    centralized creation/transition path to call rather than constructing or
    mutating PaymentAttempt rows directly.
    """

    def create_attempt(self, installment: Installment) -> PaymentAttempt:
        attempt = PaymentAttempt.objects.create(
            installment=installment,
            expected_amount=installment.expected_amount,
            currency=installment.currency,
            status=PaymentAttempt.Status.CREATED,
        )
        log_service_success(logger, "PaymentAttemptService", "create_attempt", installment_id=installment.id)
        return attempt

    def transition(
        self, attempt: PaymentAttempt, new_status: str,
        raw_provider_status: str = "", provider_message: str = "",
    ) -> PaymentAttempt:
        """
        The only sanctioned way to change PaymentAttempt.status. Idempotent:
        re-applying the attempt's current status is a no-op, not an error —
        safe for duplicate/replayed webhook deliveries (final payment
        transitions must be idempotent). Rejects any transition not in
        _PAYMENT_ATTEMPT_TRANSITIONS, in particular anything out of the
        terminal SUCCEEDED state — a provider-confirmed success can never
        change, regardless of what a later event claims.
        """
        with transaction.atomic():
            locked = PaymentAttempt.objects.select_for_update().get(pk=attempt.pk)
            if new_status == locked.status:
                log_service_success(logger, "PaymentAttemptService", "transition", attempt_id=locked.id)
                return locked
            allowed = _PAYMENT_ATTEMPT_TRANSITIONS.get(locked.status, set())
            if new_status not in allowed:
                raise InvalidStateTransitionError(
                    f"PaymentAttempt {locked.id}: cannot transition {locked.status} -> {new_status}."
                )
            locked.status = new_status
            if raw_provider_status:
                locked.raw_provider_status = raw_provider_status
            if provider_message:
                locked.provider_message = provider_message
            if new_status == PaymentAttempt.Status.LINK_CREATED and not locked.initiated_at:
                locked.initiated_at = timezone.now()
            if new_status in {PaymentAttempt.Status.SUCCEEDED, PaymentAttempt.Status.FAILED, PaymentAttempt.Status.EXPIRED}:
                locked.completed_at = timezone.now()
            locked.save()
            log_service_success(logger, "PaymentAttemptService", "transition", attempt_id=locked.id)
            return locked


_CHECKOUT_SIGNING_SALT = "apps.payments.checkout.v1"

# Phase 6 correction: the API host and the checkout-link (redirect) host are
# DIFFERENT concepts and must never be conflated. TaraClient.BASE_URL
# (integrations/payments/tara/client.py) — www.dklo.co — is where THIS
# application sends API calls (POST /tara/paymentlinks, etc.). It is NOT where
# Tara sends the CUSTOMER to pay: the documented generalLink/cardLink sample
# values point at https://taramoney.com/pay/... and https://taramoney.com/card/...
# respectively — a distinct domain. Phase 5's original allowlist
# (www.dklo.co/dklo.co) was wrong — it treated the API host as if it were also
# a valid checkout-redirect host, with no documented basis for that. Do not
# merge these two constants/concepts again.
#
# www.taramoney.com is deliberately NOT included: no documented response or
# canonicalization requirement evidences it, only the bare apex is documented.
# Add it only if a future confirmed sample shows it.
_ALLOWED_TARA_CHECKOUT_HOSTS = {"taramoney.com"}


class CheckoutResult:
    """
    Safe, customer-facing checkout outcome only. Deliberately excludes: API
    keys, businessId, webhook secret, raw provider request/response, internal
    database primary keys, another customer's data, arbitrary provider
    messages, and every payment-sharing link except the one vetted checkout
    URL (never whatsapp/telegram/dikalo/sms links).
    """

    def __init__(self, order_reference, signed_reference: str, status: str, checkout_url: Optional[str] = None, message: str = ""):
        self.order_reference = str(order_reference)
        self.signed_reference = signed_reference
        self.status = status
        self.checkout_url = checkout_url
        self.message = message


class CheckoutService:
    """
    Dedicated checkout/application service (docs/TARA_INTEGRATION_PROJECT.md
    Phase 5) — the only place that orchestrates OrderService +
    PaymentAttemptService + TaraClient together for a customer-initiated
    checkout. Provider calls never happen in models or views. Never holds a
    database transaction/row lock during the external HTTP call to Tara —
    each locked section below is short and closes before _create_link_for_attempt
    calls out to Tara, which itself runs with no open transaction.
    """

    def resolve_guest_contact(self, email: str, phone: str = "", first_name: str = "", last_name: str = "") -> Contact:
        """
        Guest checkout identity resolution — this app has no customer
        authentication (every existing @login_required view is staff-only;
        Contact has no relation to Django's User model), so checkout is
        necessarily guest, per repository evidence (Phase 5 discovery).

        Safe by construction, not by extra locking: Contact.email already has
        a DB-level unique=True constraint (apps/contacts/models.py), so
        get_or_create() is Django's own race-safe pattern here — a concurrent
        duplicate insert raises IntegrityError internally and get_or_create()
        transparently re-fetches the winner. Never overwrites an existing
        Contact's stored data (name/phone) with newly-submitted checkout
        values — "defaults" only apply on first creation. Never reveals via
        its return value or any distinguishable side effect whether the email
        already existed.
        """
        email = (email or "").strip().lower()
        phone = normalize_phone(phone)
        contact, _ = Contact.objects.get_or_create(
            email=email,
            defaults={"phone": phone, "first_name": first_name or "", "last_name": last_name or ""},
        )
        return contact

    def build_signed_reference(self, order_reference: str) -> str:
        """An opaque, signed, expiring capability — never a bare UUID/pk. See resolve_signed_reference()."""
        return signing.dumps({"order_reference": str(order_reference)}, salt=_CHECKOUT_SIGNING_SALT)

    def resolve_signed_reference(self, token: str, max_age_seconds: int = 60 * 60 * 24 * 3) -> Order:
        """Returns the Order or raises CheckoutError (expired/tampered) — a bare UUID is never sufficient authorization on its own."""
        try:
            data = signing.loads(token, salt=_CHECKOUT_SIGNING_SALT, max_age=max_age_seconds)
        except signing.SignatureExpired as e:
            raise CheckoutError("This checkout link has expired.") from e
        except signing.BadSignature as e:
            raise CheckoutError("This checkout link is invalid.") from e

        order = Order.objects.filter(reference=data.get("order_reference")).first()
        if order is None:
            raise CheckoutError("This checkout link is invalid.")
        return order

    def start_checkout(self, customer: Contact, plan_id: int, idempotency_key: str) -> CheckoutResult:
        log_service_start(logger, "CheckoutService", "start_checkout")
        base_url = self._require_public_base_url()

        order, _ = OrderService().create_order(customer, plan_id, idempotency_key)
        signed_reference = self.build_signed_reference(order.reference)

        installment = (
            order.installments
            .exclude(status__in=[Installment.Status.PAID, Installment.Status.WAIVED, Installment.Status.CANCELLED])
            .order_by("sequence")
            .first()
        )
        if installment is None:
            return CheckoutResult(order.reference, signed_reference, "no_payment_due", message="This order has no outstanding payment.")

        attempt, _ = self._get_or_create_current_attempt(installment.id)

        if attempt.status == PaymentAttempt.Status.SUCCEEDED:
            return CheckoutResult(order.reference, signed_reference, "succeeded")
        if attempt.status in (PaymentAttempt.Status.LINK_CREATED, PaymentAttempt.Status.PENDING) and attempt.general_link:
            return CheckoutResult(order.reference, signed_reference, "link_ready", checkout_url=attempt.general_link)
        if attempt.status == PaymentAttempt.Status.UNKNOWN:
            return self._recheck_unknown_attempt(order, signed_reference, attempt)

        # attempt.status == CREATED (a fresh attempt — including one just
        # created above to replace a FAILED/EXPIRED predecessor — or
        # LINK_CREATED without a stored link, defensively) -> call Tara.
        log_service_success(logger, "CheckoutService", "start_checkout", order_id=order.id)
        return self._create_link_for_attempt(order, signed_reference, installment, attempt, base_url)

    def _require_public_base_url(self) -> str:
        base_url = (getattr(settings, "PUBLIC_BASE_URL", "") or "").strip()
        if not base_url:
            raise CheckoutConfigurationError(
                "Checkout is not available right now — the application's public base URL is not configured."
            )
        if urlsplit(base_url).scheme != "https":
            raise CheckoutConfigurationError("Checkout is not available right now — the public base URL must be HTTPS.")
        return base_url

    def _get_or_create_current_attempt(self, installment_id: int) -> Tuple[PaymentAttempt, bool]:
        """
        Row-locked get-or-create: at most one non-terminal-retryable attempt
        per installment. select_for_update() serializes concurrent callers for
        the SAME installment; the DB constraint
        one_active_payment_attempt_per_installment (apps/payments/models.py)
        backstops this even against a caller that bypasses this method.

        A FAILED/EXPIRED attempt never blocks a fresh one — per PaymentAttempt's
        own docstring, "an installment may have multiple attempts (e.g. retry
        after FAILED/EXPIRED) — each gets its own unique tara_product_id". The
        old attempt is left untouched as history; a new one is simply logged
        and returned so checkout can proceed normally.
        """
        with transaction.atomic():
            installment = Installment.objects.select_for_update().get(pk=installment_id)
            existing = installment.payment_attempts.order_by("-created_at").first()
            if existing and existing.status not in (PaymentAttempt.Status.FAILED, PaymentAttempt.Status.EXPIRED):
                return existing, False
            attempt = PaymentAttemptService().create_attempt(installment)
            return attempt, True

    def _recheck_unknown_attempt(self, order: Order, signed_reference: str, attempt: PaymentAttempt) -> CheckoutResult:
        """
        Never blindly calls create_payment_link() again for an UNKNOWN attempt
        — checks transaction status with the SAME tara_product_id first. If
        that check is itself inconclusive (timeout/error/still
        PENDING/UNKNOWN), the attempt stays UNKNOWN and the customer is told
        verification is pending — no new attempt, no new link call.
        """
        try:
            client = TaraConfigService().get_client()
            status_response = client.check_transaction_status(attempt.tara_product_id)
        except (
            TaraConfigurationError, TaraCredentialError, TaraTimeoutError, TaraConnectionError,
            TaraServerError, TaraClientError, TaraMalformedResponseError,
        ):
            return CheckoutResult(order.reference, signed_reference, "verification_pending")

        normalized = status_response.normalized_status
        if normalized == TaraTransactionStatus.SUCCESS:
            self._transition_attempt_locked(attempt.id, PaymentAttempt.Status.SUCCEEDED, raw_provider_status=status_response.status[:50])
            return CheckoutResult(order.reference, signed_reference, "succeeded")
        if normalized == TaraTransactionStatus.FAILURE:
            self._transition_attempt_locked(attempt.id, PaymentAttempt.Status.FAILED, raw_provider_status=status_response.status[:50])
            return CheckoutResult(order.reference, signed_reference, "failed")
        # PENDING or still UNKNOWN -> no transition, keep the same attempt, report pending.
        return CheckoutResult(order.reference, signed_reference, "verification_pending")

    def _create_link_for_attempt(
        self, order: Order, signed_reference: str, installment: Installment, attempt: PaymentAttempt, base_url: str,
    ) -> CheckoutResult:
        from django.urls import reverse  # local import: services.py must not hard-depend on URLconf being loaded at import time

        return_url = f"{base_url}{reverse('payments:checkout_status', args=[signed_reference])}"
        webhook_url = f"{base_url}/api/tara/webhook/"  # fixed, application-owned — not yet mounted (Phase 6); see Phase 5 report

        try:
            client = TaraConfigService().get_client()
            response = client.create_payment_link(
                product_id=attempt.tara_product_id,
                product_name=order.plan_name[:255],
                product_price=installment.expected_amount,
                product_description=(
                    f"{order.plan_name} — installment {installment.sequence} of {order.installment_count}"
                )[:2000],
                web_hook_url=webhook_url,
                return_url=return_url,
            )
        except (TaraTimeoutError, TaraConnectionError, TaraServerError, TaraMalformedResponseError) as e:
            # Indeterminate — Tara may have created the link anyway. Never retry automatically; keep the same productId/attempt.
            log_service_failure(logger, "CheckoutService", "_create_link_for_attempt", e, product_id=attempt.tara_product_id)
            self._transition_attempt_locked(attempt.id, PaymentAttempt.Status.UNKNOWN)
            return CheckoutResult(order.reference, signed_reference, "verification_pending")
        except TaraProviderBusinessError as e:
            log_service_failure(logger, "CheckoutService", "_create_link_for_attempt", e, product_id=attempt.tara_product_id)
            self._transition_attempt_locked(attempt.id, PaymentAttempt.Status.FAILED)
            return CheckoutResult(order.reference, signed_reference, "failed", message="This payment could not be started. Please try again.")
        except TaraClientError as e:
            # Tara rejected the request outright (e.g. bad request shape) — definitive for THIS attempt.
            log_service_failure(logger, "CheckoutService", "_create_link_for_attempt", e, product_id=attempt.tara_product_id)
            self._transition_attempt_locked(attempt.id, PaymentAttempt.Status.FAILED)
            return CheckoutResult(order.reference, signed_reference, "failed", message="This payment could not be started. Please try again.")
        except (TaraConfigurationError, TaraCredentialError, TaraInvalidRequestError) as e:
            # Our own configuration/programming problem, not a verdict on the
            # payment itself — the attempt was never actually sent to Tara, so
            # it stays CREATED (never marked FAILED/UNKNOWN for this). Report
            # "awaiting_payment", matching what the status page will show on
            # reload for a still-CREATED attempt — not "verification_pending",
            # which implies an indeterminate outcome from an attempt that was
            # actually sent.
            log_service_failure(logger, "CheckoutService", "_create_link_for_attempt", e, product_id=attempt.tara_product_id)
            return CheckoutResult(
                order.reference, signed_reference, "awaiting_payment",
                message="Checkout is temporarily unavailable. Please try again shortly.",
            )

        self._persist_link_result_locked(attempt.id, response)
        checkout_url = self._select_safe_checkout_url(response)
        if checkout_url is None:
            # Tara reported success but gave us no link we're willing to redirect to automatically.
            return CheckoutResult(order.reference, signed_reference, "verification_pending")
        return CheckoutResult(order.reference, signed_reference, "link_ready", checkout_url=checkout_url)

    def _persist_link_result_locked(self, attempt_id: int, response) -> PaymentAttempt:
        with transaction.atomic():
            attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt_id)
            if attempt.status != PaymentAttempt.Status.CREATED:
                return attempt  # already handled by a concurrent/duplicate call — do not overwrite
            attempt.whatsapp_link = response.whatsapp_link or ""
            attempt.telegram_link = response.telegram_link or ""
            attempt.dikalo_link = response.dikalo_link or ""
            attempt.general_link = response.general_link or ""
            attempt.card_link = response.card_link or ""
            attempt.sms_link = response.sms_link or ""
            attempt.provider_message = (response.message or "")[:500]
            # expires_at deliberately left unset — Tara does not document link expiry.
            attempt.save(update_fields=[
                "whatsapp_link", "telegram_link", "dikalo_link", "general_link",
                "card_link", "sms_link", "provider_message", "updated_at",
            ])
            PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED, raw_provider_status=response.status[:50])
            return attempt

    def _transition_attempt_locked(self, attempt_id: int, new_status: str, raw_provider_status: str = "") -> None:
        with transaction.atomic():
            attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt_id)
            try:
                PaymentAttemptService().transition(attempt, new_status, raw_provider_status=raw_provider_status)
            except InvalidStateTransitionError:
                pass  # already resolved to a terminal/other state by a concurrent call — nothing to do

    @staticmethod
    def _select_safe_checkout_url(response) -> Optional[str]:
        """
        generalLink only — never whatsappLink/telegramLink/dikaloLink/smsLink
        (not safe automatic-redirect targets). Requires HTTPS, an allowlisted
        host, the default port, and no embedded userinfo.
        """
        candidate = response.general_link
        if not candidate:
            return None
        parts = urlsplit(candidate)
        if parts.scheme != "https":
            return None
        if parts.username or parts.password:
            return None
        if parts.hostname not in _ALLOWED_TARA_CHECKOUT_HOSTS:
            return None
        if parts.port not in (None, 443):
            return None
        return candidate


class PaymentCreditService:
    """
    Centralized, atomic, idempotent application of a VERIFIED payment outcome
    (Phase 6, docs/TARA_INTEGRATION_PROJECT.md). The only path allowed to
    transition PaymentAttempt -> SUCCEEDED / Installment -> PAID / Order status
    from a provider confirmation. Called only by WebhookProcessingService
    after it has independently confirmed the outcome via
    TaraClient.check_transaction_status() — never from webhook fields alone.
    Never calls ClickFunnels, never sends a confirmation message (both
    explicitly out of Phase 6's scope).
    """

    def apply_verified_success(
        self, attempt_id: int, tara_payment_id: Optional[str] = None, verified_amount: Optional[Decimal] = None,
        on_applied: Optional[Callable[[PaymentAttempt, bool], None]] = None,
    ) -> PaymentAttempt:
        """
        Steps (all in one short transaction with row locks, per the project
        brief): lock PaymentAttempt -> lock Installment -> lock Order ->
        re-check current state -> verify amount/currency invariants -> set
        tara_payment_id if present and not conflicting -> transition attempt
        to SUCCEEDED -> transition installment to PAID with paid_amount ->
        set paid_at (done inside InstallmentService.transition) -> update
        Order status -> create durable follow-up work records (Phase 7,
        docs/TARA_INTEGRATION_PROJECT.md) -> return.

        Phase 7 follow-ups happen only on this — the first-time — path: the
        early idempotent-no-op return above (attempt already SUCCEEDED) never
        reaches this code, so a duplicate verified webhook can never create a
        second PaymentConfirmation or a second enrollment/ProvisioningRequest.
        Both follow-up records are created here, inside this same
        transaction, as durable DB rows only — no email is sent and no
        ClickFunnels call is made in this method; that happens afterward, out
        of transaction, in PaymentConfirmationDeliveryService /
        ProvisioningService (via management commands).

        `on_applied(attempt, newly_applied)` (Phase 8), if given, is called
        inside this same transaction right before returning — on BOTH the
        idempotent no-op path (newly_applied=False) and the normal path
        (newly_applied=True) — so a caller that must write its own audit
        record atomically with this state change (e.g.
        PaymentAttemptAdministrationService.check_tara_status) can do so
        without duplicating this method's logic. Never used by the webhook
        path, which has no such requirement.
        """
        with transaction.atomic():
            attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt_id)
            installment = Installment.objects.select_for_update().get(pk=attempt.installment_id)
            order = Order.objects.select_for_update().get(pk=installment.order_id)

            if attempt.status == PaymentAttempt.Status.SUCCEEDED:
                # Idempotent no-op — duplicate verified SUCCESS must credit exactly once.
                log_service_success(logger, "PaymentCreditService", "apply_verified_success", attempt_id=attempt.id)
                if on_applied is not None:
                    on_applied(attempt, False)
                return attempt

            if tara_payment_id:
                conflict_exists = (
                    PaymentAttempt.objects.filter(tara_payment_id=tara_payment_id).exclude(pk=attempt.pk).exists()
                )
                if conflict_exists:
                    raise PaymentIdConflictError(
                        f"Tara paymentId is already linked to a different PaymentAttempt (attempt {attempt.id} not credited)."
                    )
                if attempt.tara_payment_id and attempt.tara_payment_id != tara_payment_id:
                    raise PaymentIdConflictError(
                        f"PaymentAttempt {attempt.id} is already linked to a different Tara paymentId."
                    )
                if not attempt.tara_payment_id:
                    attempt.tara_payment_id = tara_payment_id
                    attempt.save(update_fields=["tara_payment_id", "updated_at"])

            expected_amount = installment.expected_amount

            PaymentAttemptService().transition(attempt, PaymentAttempt.Status.SUCCEEDED)
            InstallmentService().transition(installment, Installment.Status.PAID, paid_amount=expected_amount)
            self._update_order_status(order)

            PaymentConfirmationService().create_confirmation(attempt, installment, order)

            if order.is_access_eligible:
                from apps.provisioning.services import ProvisioningService

                ProvisioningService().create_request_from_order(order)

            log_service_success(
                logger, "PaymentCreditService", "apply_verified_success",
                attempt_id=attempt.id, installment_id=installment.id,
            )
            attempt.refresh_from_db()
            if on_applied is not None:
                on_applied(attempt, True)
            return attempt

    def apply_verified_failure(
        self, attempt_id: int, on_applied: Optional[Callable[[PaymentAttempt], None]] = None,
    ) -> PaymentAttempt:
        """
        Transitions only where the state machine permits — never erases a
        previous SUCCEEDED attempt/PAID installment (InvalidStateTransitionError
        is swallowed, matching "only if allowed"). Never touches Order.status;
        never automatically cancels the order.

        `on_applied(attempt)` (Phase 8), if given, is called inside this same
        transaction right before returning — see apply_verified_success's
        docstring for why.
        """
        with transaction.atomic():
            attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt_id)
            installment = Installment.objects.select_for_update().get(pk=attempt.installment_id)

            try:
                PaymentAttemptService().transition(attempt, PaymentAttempt.Status.FAILED)
            except InvalidStateTransitionError:
                pass  # e.g. already SUCCEEDED — never overwritten

            try:
                InstallmentService().transition(installment, Installment.Status.FAILED)
            except InvalidStateTransitionError:
                pass  # e.g. already PAID via a different attempt

            log_service_success(logger, "PaymentCreditService", "apply_verified_failure", attempt_id=attempt.id)
            attempt.refresh_from_db()
            if on_applied is not None:
                on_applied(attempt)
            return attempt

    def _update_order_status(self, order: Order) -> None:
        """
        PENDING -> ACTIVE once any installment is PAID, or once every
        installment is settled (PAID/WAIVED) even without a payment (Phase 8
        waiver-only completion — _ORDER_TRANSITIONS has no direct
        PENDING -> COMPLETED hop, so this must pass through ACTIVE first);
        ACTIVE/PAST_DUE -> COMPLETED once every installment is PAID or
        WAIVED. Never rewrites a CANCELLED/SUSPENDED order — payment history
        stays true regardless.
        """
        if order.status in (Order.Status.CANCELLED, Order.Status.SUSPENDED):
            return

        installments = list(order.installments.all())
        any_paid = any(i.status == Installment.Status.PAID for i in installments)
        all_settled = bool(installments) and all(
            i.status in (Installment.Status.PAID, Installment.Status.WAIVED) for i in installments
        )

        order_service = OrderService()
        if (any_paid or all_settled) and order.status == Order.Status.PENDING:
            order = order_service.transition(order, Order.Status.ACTIVE)

        if all_settled and order.status != Order.Status.COMPLETED:
            order_service.transition(order, Order.Status.COMPLETED)


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class PaymentConfirmationService:
    """
    Creates the durable PaymentConfirmation work record (Phase 7,
    docs/TARA_INTEGRATION_PROJECT.md). Called only by PaymentCreditService.
    apply_verified_success(), inside its own atomic transaction, once per
    newly-verified successful Installment. Never sends the email itself —
    that's PaymentConfirmationDeliveryService's job, run by a separate
    worker/management command outside any payment transaction.
    """

    def create_confirmation(self, attempt: PaymentAttempt, installment: Installment, order: Order) -> PaymentConfirmation:
        recipient = order.customer.email or ""
        idempotency_key = _sha256_hex(
            f"{PaymentConfirmation.NotificationType.PAYMENT_CONFIRMATION}|{attempt.id}|"
            f"{PaymentConfirmation.Channel.EMAIL}|{recipient}".encode()
        )
        confirmation, created = PaymentConfirmation.objects.get_or_create(
            idempotency_key=idempotency_key,
            defaults=dict(
                notification_type=PaymentConfirmation.NotificationType.PAYMENT_CONFIRMATION,
                contact=order.customer,
                order=order,
                installment=installment,
                payment_attempt=attempt,
                channel=PaymentConfirmation.Channel.EMAIL,
                recipient_snapshot=recipient,
                status=PaymentConfirmation.Status.PENDING,
            ),
        )
        log_service_success(
            logger, "PaymentConfirmationService", "create_confirmation",
            attempt_id=attempt.id, confirmation_id=confirmation.id, created=created,
        )
        return confirmation


class PaymentConfirmationDeliveryService:
    """
    Worker-side processing for PaymentConfirmation (Phase 7) — renders and
    sends the confirmation email OUTSIDE any payment transaction. Invoked by
    apps/payments/management/commands/process_payment_confirmations.py (and,
    later, an hourly scheduler — Phase 9, out of scope here). An email
    failure here never touches PaymentAttempt/Installment/Order — payment
    success is fully preserved regardless of delivery outcome.
    """

    _CLAIMABLE_STATUSES = (PaymentConfirmation.Status.PENDING, PaymentConfirmation.Status.FAILED)

    def process_pending(self, limit: int = 100) -> int:
        now = timezone.now()
        ids = list(
            PaymentConfirmation.objects.filter(status__in=self._CLAIMABLE_STATUSES)
            .filter(models.Q(next_attempt_at__isnull=True) | models.Q(next_attempt_at__lte=now))
            .order_by("created_at")
            .values_list("id", flat=True)[:limit]
        )
        processed = 0
        for confirmation_id in ids:
            self._process_one(confirmation_id)
            processed += 1
        return processed

    def _process_one(self, confirmation_id: int) -> None:
        claimed = self._claim(confirmation_id)
        if claimed is None:
            return

        recipient = claimed.recipient_snapshot
        if not recipient:
            self._finish_manual_review(
                claimed.id, PaymentConfirmation.FailureCategory.INVALID_RECIPIENT,
            )
            return

        try:
            subject, body = self._render(claimed)
            send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [recipient], fail_silently=False)
        except (smtplib.SMTPException, TimeoutError, ConnectionError) as e:
            log_service_failure(logger, "PaymentConfirmationDeliveryService", "_process_one", e, confirmation_id=confirmation_id)
            self._finish_retryable_failure(claimed.id, PaymentConfirmation.FailureCategory.TRANSIENT_DELIVERY_ERROR)
            return
        except Exception as e:
            log_service_failure(logger, "PaymentConfirmationDeliveryService", "_process_one", e, confirmation_id=confirmation_id)
            self._finish_manual_review(claimed.id, PaymentConfirmation.FailureCategory.CONFIGURATION_ERROR)
            return

        self._mark_sent(claimed.id)

    def _claim(self, confirmation_id: int) -> Optional[PaymentConfirmation]:
        with transaction.atomic():
            try:
                confirmation = PaymentConfirmation.objects.select_for_update().get(pk=confirmation_id)
            except PaymentConfirmation.DoesNotExist:
                return None
            if confirmation.status not in self._CLAIMABLE_STATUSES:
                return None
            if confirmation.attempt_count >= PAYMENT_CONFIRMATION_MAX_ATTEMPTS:
                confirmation.status = PaymentConfirmation.Status.MANUAL_REVIEW
                confirmation.last_failure_category = PaymentConfirmation.FailureCategory.MAX_ATTEMPTS_EXCEEDED
                confirmation.next_attempt_at = None
                confirmation.save(update_fields=["status", "last_failure_category", "next_attempt_at", "updated_at"])
                return None
            confirmation.status = PaymentConfirmation.Status.SENDING
            confirmation.attempt_count += 1
            confirmation.save(update_fields=["status", "attempt_count", "updated_at"])
            return confirmation

    def _mark_sent(self, confirmation_id: int) -> None:
        with transaction.atomic():
            confirmation = PaymentConfirmation.objects.select_for_update().get(pk=confirmation_id)
            confirmation.status = PaymentConfirmation.Status.SENT
            confirmation.sent_at = timezone.now()
            confirmation.last_failure_category = ""
            confirmation.next_attempt_at = None
            confirmation.save(update_fields=["status", "sent_at", "last_failure_category", "next_attempt_at", "updated_at"])

    def _finish_retryable_failure(self, confirmation_id: int, category: str) -> None:
        with transaction.atomic():
            confirmation = PaymentConfirmation.objects.select_for_update().get(pk=confirmation_id)
            if confirmation.attempt_count >= PAYMENT_CONFIRMATION_MAX_ATTEMPTS:
                confirmation.status = PaymentConfirmation.Status.MANUAL_REVIEW
                confirmation.last_failure_category = PaymentConfirmation.FailureCategory.MAX_ATTEMPTS_EXCEEDED
                confirmation.next_attempt_at = None
            else:
                backoff_index = min(
                    confirmation.attempt_count - 1, len(PAYMENT_CONFIRMATION_RETRY_BACKOFF_MINUTES) - 1,
                )
                confirmation.status = PaymentConfirmation.Status.FAILED
                confirmation.last_failure_category = category
                confirmation.next_attempt_at = timezone.now() + timedelta(
                    minutes=PAYMENT_CONFIRMATION_RETRY_BACKOFF_MINUTES[backoff_index]
                )
            confirmation.save(update_fields=["status", "last_failure_category", "next_attempt_at", "updated_at"])

    def _finish_manual_review(self, confirmation_id: int, category: str) -> None:
        with transaction.atomic():
            confirmation = PaymentConfirmation.objects.select_for_update().get(pk=confirmation_id)
            confirmation.status = PaymentConfirmation.Status.MANUAL_REVIEW
            confirmation.last_failure_category = category
            confirmation.next_attempt_at = None
            confirmation.save(update_fields=["status", "last_failure_category", "next_attempt_at", "updated_at"])

    def _render(self, confirmation: PaymentConfirmation) -> Tuple[str, str]:
        order = confirmation.order
        installment = confirmation.installment
        contact = confirmation.contact
        remaining = order.total_expected_amount - order.total_paid_amount
        next_due_date = (
            order.installments.filter(
                status__in=[Installment.Status.SCHEDULED, Installment.Status.DUE, Installment.Status.PENDING]
            )
            .order_by("sequence")
            .values_list("due_date", flat=True)
            .first()
        )
        customer_name = f"{contact.first_name} {contact.last_name}".strip()
        context = {
            "customer_name": customer_name,
            "amount_received": installment.paid_amount or installment.expected_amount,
            "currency": installment.currency,
            "order_reference": str(order.reference),
            "plan_or_course_name": order.course_name,
            "installment_number": installment.sequence,
            "installment_count": order.installment_count,
            "remaining_balance": remaining,
            "remaining_balance_positive": remaining > 0,
            "next_installment_due_date": next_due_date,
            "course_access_status": "now accessible" if order.is_access_eligible else "not yet accessible",
        }
        subject = f"Payment received — installment {installment.sequence} of {order.installment_count}"
        body = render_to_string("payments/email/payment_confirmation.txt", context)
        return subject, body


class WebhookProcessingService:
    """
    Entry point for POST /api/tara/webhook/ (Phase 6,
    docs/TARA_INTEGRATION_PROJECT.md). CRITICAL TRUST CONSTRAINT: Tara's
    documentation does not define a signature header, algorithm, signed byte
    sequence, encoding, timestamp, or replay window (see
    integrations/payments/tara/signature.py's docstring) — every webhook this
    method receives is UNTRUSTED input, used ONLY as a trigger to call
    TaraClient.check_transaction_status() server-to-server. No field on the
    incoming payload is ever sufficient, alone, to credit a payment.

    Correlates a new-style payment ONLY by exact PaymentAttempt.tara_product_id
    — never by phoneNumber, amount, or collectionId. An event that can't
    correlate this way is recorded as UNCORRELATED, not guessed at.

    Also the single place both this webhook route and a future reconciliation
    job (not built in this phase) are expected to call — keeping verification
    logic out of the view/router entirely.
    """

    def process_webhook(self, raw_body: bytes) -> TaraWebhookEvent:
        payload, payload_digest = self._parse_payload(raw_body)

        dedup_key = self._compute_dedup_key(payload, payload_digest)
        event, created = TaraWebhookEvent.objects.get_or_create(
            dedup_key=dedup_key,
            defaults={
                "business_id": payload.business_id,
                "tara_product_id": payload.product_id or "",
                "tara_payment_id": payload.payment_id or "",
                "raw_provider_status": payload.status[:50],
                "payload_digest": payload_digest,
                "provider_creation_date": payload.creation_date,
                "provider_change_date": payload.change_date,
            },
        )
        if not created:
            # Identical delivery (or a genuine retry of an already-durably-recorded
            # event) — harmless. Do not reprocess; the first delivery already
            # owns this dedup_key's outcome.
            log_service_success(logger, "WebhookProcessingService", "process_webhook", processing_status=event.processing_status)
            return event

        log_service_start(logger, "WebhookProcessingService", "process_webhook")

        active_config = TaraConfigService().get_active_config()
        if active_config is None or not hmac_module.compare_digest(payload.business_id, active_config.business_id or ""):
            event.processing_status = TaraWebhookEvent.ProcessingStatus.REJECTED
            event.failure_category = TaraWebhookEvent.FailureCategory.BUSINESS_ID_MISMATCH
            event.processed_at = timezone.now()
            event.save()
            return event

        attempt = None
        if payload.product_id:
            attempt = (
                PaymentAttempt.objects.filter(tara_product_id=payload.product_id)
                .select_related("installment__order")
                .first()
            )

        if attempt is None:
            event.processing_status = TaraWebhookEvent.ProcessingStatus.UNCORRELATED
            event.failure_category = (
                TaraWebhookEvent.FailureCategory.UNKNOWN_PRODUCT_ID if payload.product_id
                else TaraWebhookEvent.FailureCategory.UNCORRELATED
            )
            event.processed_at = timezone.now()
            event.save()
            log_service_success(logger, "WebhookProcessingService", "process_webhook", processing_status=event.processing_status)
            return event

        event.payment_attempt = attempt
        event.save(update_fields=["payment_attempt", "updated_at"])

        self._verify_and_apply(event, attempt, payload)
        return event

    def _parse_payload(self, raw_body: bytes) -> Tuple[TaraWebhookPayload, str]:
        if len(raw_body) > TARA_WEBHOOK_MAX_BODY_BYTES:
            raise WebhookRejectedError("Webhook body exceeded the maximum allowed size.")
        try:
            data = json.loads(raw_body or b"")
        except (json.JSONDecodeError, ValueError) as e:
            raise WebhookRejectedError("Malformed JSON.") from e
        if not isinstance(data, dict):
            raise WebhookRejectedError("Webhook payload was not a JSON object.")
        try:
            payload = TaraWebhookPayload.model_validate(data)
        except PydanticValidationError as e:
            raise WebhookRejectedError("Webhook payload did not match the documented shape.") from e
        return payload, _sha256_hex(raw_body)

    def _compute_dedup_key(self, payload: TaraWebhookPayload, payload_digest: str) -> str:
        change_date_repr = payload.change_date.isoformat() if payload.change_date else ""
        raw = f"{payload.payment_id or ''}|{payload.product_id or ''}|{payload.status}|{change_date_repr}|{payload_digest}"
        return _sha256_hex(raw.encode())

    def _verify_and_apply(self, event: TaraWebhookEvent, attempt: PaymentAttempt, payload: TaraWebhookPayload) -> None:
        event.verification_mode = TaraWebhookEvent.VerificationMode.SERVER_TO_SERVER
        event.processing_attempts += 1

        try:
            client = TaraConfigService().get_client()
            status_response = client.check_transaction_status(attempt.tara_product_id)
        except (TaraConfigurationError, TaraCredentialError) as e:
            log_service_failure(logger, "WebhookProcessingService", "_verify_and_apply", e, product_id=attempt.tara_product_id)
            event.verification_result = TaraWebhookEvent.VerificationResult.SKIPPED
            event.processing_status = TaraWebhookEvent.ProcessingStatus.FAILED
            event.failure_category = TaraWebhookEvent.FailureCategory.PROVIDER_LOOKUP_INDETERMINATE
            event.save()
            return
        except (TaraTimeoutError, TaraConnectionError, TaraServerError, TaraClientError, TaraMalformedResponseError) as e:
            # Indeterminate — recorded for reconciliation, no final transition.
            log_service_failure(logger, "WebhookProcessingService", "_verify_and_apply", e, product_id=attempt.tara_product_id)
            event.verification_result = TaraWebhookEvent.VerificationResult.UNKNOWN
            event.processing_status = TaraWebhookEvent.ProcessingStatus.FAILED
            event.failure_category = TaraWebhookEvent.FailureCategory.PROVIDER_LOOKUP_INDETERMINATE
            event.save()
            return

        if status_response.product_id != attempt.tara_product_id:
            event.verification_result = TaraWebhookEvent.VerificationResult.UNKNOWN
            event.processing_status = TaraWebhookEvent.ProcessingStatus.FAILED
            event.failure_category = TaraWebhookEvent.FailureCategory.MALFORMED
            event.processed_at = timezone.now()
            event.save()
            return

        event.raw_provider_status = status_response.status[:50]
        normalized = status_response.normalized_status

        if normalized == TaraTransactionStatus.SUCCESS:
            if payload.amount is not None and payload.amount != attempt.expected_amount:
                # Webhook amount is never proof by itself, but a present-and-differing
                # value is an anomaly-review signal — do not credit automatically.
                event.verification_result = TaraWebhookEvent.VerificationResult.SUCCESS
                event.processing_status = TaraWebhookEvent.ProcessingStatus.FAILED
                event.failure_category = TaraWebhookEvent.FailureCategory.AMOUNT_MISMATCH
                event.processed_at = timezone.now()
                event.save()
                return

            try:
                PaymentCreditService().apply_verified_success(
                    attempt.id, tara_payment_id=payload.payment_id, verified_amount=payload.amount,
                )
                event.verification_result = TaraWebhookEvent.VerificationResult.SUCCESS
                event.processing_status = TaraWebhookEvent.ProcessingStatus.PROCESSED
            except PaymentIdConflictError as e:
                log_service_failure(logger, "WebhookProcessingService", "_verify_and_apply", e, product_id=attempt.tara_product_id)
                event.verification_result = TaraWebhookEvent.VerificationResult.SUCCESS
                event.processing_status = TaraWebhookEvent.ProcessingStatus.FAILED
                event.failure_category = TaraWebhookEvent.FailureCategory.PAYMENT_ID_CONFLICT
        elif normalized == TaraTransactionStatus.FAILURE:
            PaymentCreditService().apply_verified_failure(attempt.id)
            event.verification_result = TaraWebhookEvent.VerificationResult.FAILURE
            event.processing_status = TaraWebhookEvent.ProcessingStatus.PROCESSED
        elif normalized == TaraTransactionStatus.PENDING:
            event.verification_result = TaraWebhookEvent.VerificationResult.PENDING
            event.processing_status = TaraWebhookEvent.ProcessingStatus.PROCESSED
        else:
            event.verification_result = TaraWebhookEvent.VerificationResult.UNKNOWN
            event.processing_status = TaraWebhookEvent.ProcessingStatus.PROCESSED

        event.processed_at = timezone.now()
        event.save()
        log_service_success(
            logger, "WebhookProcessingService", "_verify_and_apply",
            product_id=attempt.tara_product_id, normalized_result=normalized.value,
        )
