import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction

from shared.models import TimeStampedModel


class TaraConfig(TimeStampedModel):
    """
    Single-tenant Tara credential configuration (see docs/TARA_INTEGRATION_PROJECT.md
    Phase 2). api_key/webhook_secret are always stored encrypted (shared/security.py,
    Fernet authenticated encryption) and are write-only from the admin — see
    apps/payments/admin.py::TaraConfigForm, which deliberately excludes them from
    Meta.fields so Django's ModelForm machinery never overwrites them with a blank
    submission; only TaraConfigService.update_credentials() may change them, and
    only when given a non-empty new value.

    No safe, documented, read-only Tara endpoint exists yet to verify credentials
    against (see docs/TARA_API_CONTRACT.md / Phase 0 discovery) — validation_status
    intentionally has no code path that sets it to VALID in this phase; every
    configuration stays PENDING (i.e. unverified) until a later phase confirms one.
    """

    class ValidationStatus(models.TextChoices):
        PENDING = "PENDING", "Pending (unverified — no safe Tara check confirmed yet)"
        VALID = "VALID", "Valid"
        INVALID = "INVALID", "Invalid"

    name = models.CharField(max_length=255, help_text="A descriptive name for this configuration.")
    business_id = models.CharField(
        max_length=255, default="",
        help_text="Tara businessId. Sent openly (not encrypted) in every Tara API call — "
                   "not a credential, per docs/Tara_API_Reference_Technique.docx.",
    )
    is_active = models.BooleanField(
        default=False, db_index=True,
        help_text="Enabled/active configuration. Only one configuration may be active at a time.",
    )

    # Encrypted (Fernet). Authenticates OUTBOUND calls TO Tara. Write-only from the admin.
    api_key = models.TextField(blank=True)
    # Encrypted (Fernet). Verifies INBOUND webhook calls FROM Tara — never the same secret as api_key. Write-only from the admin.
    webhook_secret = models.TextField(blank=True)

    validation_status = models.CharField(
        max_length=20,
        choices=ValidationStatus.choices,
        default=ValidationStatus.PENDING,
        db_index=True,
    )
    raw_payload = models.JSONField(default=dict, blank=True, help_text="Unused pending a confirmed safe verification endpoint.")

    class Meta:
        verbose_name = "Tara Configuration"
        verbose_name_plural = "Tara Configurations"
        constraints = [
            models.UniqueConstraint(
                fields=["is_active"], condition=models.Q(is_active=True),
                name="unique_active_tara_config",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.validation_status})"

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.is_active:
                TaraConfig.objects.filter(is_active=True).exclude(pk=self.pk).update(is_active=False)
            super().save(*args, **kwargs)


class ReconciliationRun(TimeStampedModel):
    """
    Bookkeeping for the Phase 9 hourly reconciliation run
    (run_hourly_reconciliation, apps/payments/reconciliation_services.py::
    ReconciliationService). Only safe counters are stored — no raw provider
    responses, secrets, payment URLs, or customer PII.
    """

    class RunStatus(models.TextChoices):
        RUNNING = "RUNNING", "Running"
        SUCCESS = "SUCCESS", "Success"
        PARTIAL_FAILURE = "PARTIAL_FAILURE", "Partial Failure"
        FAILED = "FAILED", "Failed"
        SKIPPED_OVERLAPPING = "SKIPPED_OVERLAPPING", "Skipped (overlapping run)"

    started_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)

    run_status = models.CharField(max_length=30, choices=RunStatus.choices, default=RunStatus.RUNNING, db_index=True)

    attempts_examined = models.PositiveIntegerField(default=0)
    status_checks = models.PositiveIntegerField(default=0)
    verified_successes = models.PositiveIntegerField(default=0)
    verified_failures = models.PositiveIntegerField(default=0)
    still_pending_or_unknown = models.PositiveIntegerField(default=0)

    confirmation_jobs_processed = models.PositiveIntegerField(default=0)
    confirmation_jobs_sent = models.PositiveIntegerField(default=0)
    confirmation_jobs_failed = models.PositiveIntegerField(default=0)

    provisioning_jobs_processed = models.PositiveIntegerField(default=0)
    provisioning_jobs_completed = models.PositiveIntegerField(default=0)
    provisioning_jobs_failed = models.PositiveIntegerField(default=0)

    missing_work_repaired = models.PositiveIntegerField(default=0)
    uncorrelated_transaction_list_records = models.PositiveIntegerField(default=0)
    safe_error_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"Run @ {self.started_at} ({self.run_status})"


class PaymentPlan(TimeStampedModel):
    """
    Administrator-configured commercial terms linked to exactly one existing
    course — see docs/TARA_INTEGRATION_PROJECT.md Phase 1. Orders (Phase 3)
    must copy every pricing-relevant field below into an immutable snapshot at
    creation time; editing a plan after that point must never retroactively
    change an existing order's terms.
    """

    class Currency(models.TextChoices):
        XAF = "XAF", "XAF — Central Africa CFA Franc"
        XOF = "XOF", "XOF — West Africa CFA Franc"

    class AccessPolicy(models.TextChoices):
        FIRST_INSTALLMENT = "FIRST_INSTALLMENT", "After first verified installment"
        FULL_PAYMENT = "FULL_PAYMENT", "After full payment"

    code = models.SlugField(
        max_length=100, unique=True,
        help_text="Stable internal identifier (e.g. for future checkout/order references). Not shown to customers.",
    )
    name = models.CharField(max_length=255, help_text="Administrator-facing and customer-facing display name.")
    description = models.TextField(blank=True)

    course = models.ForeignKey(
        "courses.Course", on_delete=models.PROTECT, related_name="payment_plans",
        help_text="The course this plan grants access to. Selected from the existing course catalog only.",
    )

    currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.XAF)
    installment_count = models.PositiveIntegerField(
        default=1, validators=[MinValueValidator(1)],
        help_text="Number of installments. Use 1 for a single one-time payment.",
    )
    installment_amount = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Amount per installment, in the currency above. The total is computed, not entered directly.",
    )
    installment_interval_days = models.PositiveIntegerField(
        default=30,
        help_text="Days between installments after the first. Ignored when installment_count is 1.",
    )

    access_policy = models.CharField(
        max_length=30, choices=AccessPolicy.choices, default=AccessPolicy.FULL_PAYMENT,
        help_text="When course access becomes eligible. Never inferred from name, price, or installment count.",
    )

    is_active = models.BooleanField(
        default=False, db_index=True,
        help_text="Published/purchasable by checkout. Unlike Tara/ClickFunnels configuration, multiple plans may be active at once.",
    )
    display_order = models.PositiveIntegerField(default=0, db_index=True)

    class Meta:
        ordering = ["display_order", "name"]
        verbose_name = "Payment Plan"
        verbose_name_plural = "Payment Plans"

    def __str__(self):
        return f"{self.name} ({self.code})"

    @property
    def computed_total(self) -> Decimal:
        """Deterministically derived — never stored, so it can never drift from installment_count/installment_amount."""
        amount = self.installment_amount or Decimal("0")
        return amount * self.installment_count

    def clean(self):
        errors = {}
        if self.installment_count and self.installment_count > 1 and self.installment_interval_days < 1:
            errors["installment_interval_days"] = "Must be at least 1 day when there is more than one installment."
        if errors:
            raise ValidationError(errors)


class Order(TimeStampedModel):
    """
    A customer's purchase of a frozen snapshot of one PaymentPlan, taken at the
    moment of purchase (see OrderService.create_order() in services.py — the
    only code path allowed to create an Order or write its snapshot fields).

    `plan` and `course` below are live FKs kept for traceability/reporting
    only — NEVER read them for pricing/terms/course identity. Every
    commercial/course fact that matters for this order's lifetime is frozen
    onto this row at creation time and must never be recomputed from the live
    plan/course, which may be edited independently afterward (a PaymentPlan's
    price, course, schedule, or access policy can all change after an order
    already exists against it — see docs/TARA_INTEGRATION_PROJECT.md Phase 3
    "Historical requirements").
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        ACTIVE = "ACTIVE", "Active"
        PAST_DUE = "PAST_DUE", "Past Due"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"
        SUSPENDED = "SUSPENDED", "Suspended"

    reference = models.UUIDField(
        default=uuid.uuid4, editable=False, unique=True, db_index=True,
        help_text="Opaque public identifier — safe to use in URLs/return pages. The numeric pk is not.",
    )

    customer = models.ForeignKey("contacts.Contact", on_delete=models.PROTECT, related_name="orders")
    plan = models.ForeignKey(PaymentPlan, on_delete=models.PROTECT, related_name="orders")
    course = models.ForeignKey(
        "courses.Course", on_delete=models.PROTECT, related_name="orders",
        help_text="Set once at creation from the plan's course at that moment. Never reassigned afterward, even if the plan's course later changes.",
    )

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    idempotency_key = models.CharField(
        max_length=255, unique=True, db_index=True,
        help_text="Caller-provided checkout idempotency key — a repeated call with this key returns the same Order.",
    )

    # --- Frozen snapshot of purchased terms — see class docstring. Written
    # once by OrderService.create_order(); nothing else should ever set these. ---
    plan_code = models.CharField(max_length=100)
    plan_name = models.CharField(max_length=255)
    course_cf_id = models.CharField(max_length=255)
    course_name = models.CharField(max_length=255)
    currency = models.CharField(max_length=3)
    installment_count = models.PositiveIntegerField()
    installment_amount = models.DecimalField(max_digits=12, decimal_places=2)
    installment_interval_days = models.PositiveIntegerField()
    total_expected_amount = models.DecimalField(max_digits=12, decimal_places=2)
    access_policy = models.CharField(max_length=30, choices=PaymentPlan.AccessPolicy.choices)

    class ManualDisposition(models.TextChoices):
        """
        Internal business-review status (Phase 8, docs/TARA_INTEGRATION_PROJECT.md)
        — deliberately separate from `status` above. Never overwrites a
        provider fact or a financial state; purely an administrator-facing
        annotation set only via OrderAdministrationService.apply_manual_disposition(),
        which requires a mandatory reason and writes an AdminAuditLog entry.
        Bounded to confirmed business needs only — no speculative REFUNDED
        state, since no refund behavior exists in this phase.
        """
        NONE = "NONE", "—"
        NEEDS_REVIEW = "NEEDS_REVIEW", "Needs Review"
        APPROVED_FOR_RETRY = "APPROVED_FOR_RETRY", "Approved for Retry"
        DENIED_BY_ADMIN = "DENIED_BY_ADMIN", "Denied by Admin"
        CUSTOMER_CANCELLED = "CUSTOMER_CANCELLED", "Customer Cancelled"
        DISPUTED = "DISPUTED", "Disputed"

    manual_disposition = models.CharField(
        max_length=30, choices=ManualDisposition.choices, default=ManualDisposition.NONE, db_index=True,
        help_text="Administrator business annotation only — never read by any state-transition or eligibility rule.",
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=models.Q(total_expected_amount__gt=0), name="order_total_positive"),
            models.CheckConstraint(condition=models.Q(installment_count__gte=1), name="order_installment_count_positive"),
            models.CheckConstraint(
                condition=models.Q(status__in=["PENDING", "ACTIVE", "PAST_DUE", "COMPLETED", "CANCELLED", "SUSPENDED"]),
                name="order_status_valid",
            ),
        ]
        permissions = [
            ("cancel_order", "Can cancel an order (Phase 8 administrative action)"),
            ("apply_manual_disposition", "Can apply a manual business disposition to an order"),
            ("freeze_enrollment", "Can freeze (suspend) a customer's ClickFunnels course enrollment"),
            ("resume_enrollment", "Can resume a previously frozen ClickFunnels course enrollment"),
        ]

    def __str__(self):
        return f"Order {self.reference} — {self.plan_name} ({self.status})"

    @property
    def total_paid_amount(self) -> Decimal:
        """Deterministic from live Installment state — never stored, never can drift."""
        result = self.installments.filter(status=Installment.Status.PAID).aggregate(total=models.Sum("paid_amount"))
        return result["total"] or Decimal("0")

    @property
    def is_access_eligible(self) -> bool:
        """
        Deterministic from the frozen access_policy snapshot plus live
        Installment PAID/WAIVED state — never inferred from plan name, price,
        or installment count (per the project brief's explicit access-policy
        rule). FULL_PAYMENT treats WAIVED the same as PAID — an installment an
        administrator has waived (Phase 8, docs/TARA_INTEGRATION_PROJECT.md)
        is a settled business decision, not an outstanding balance — matching
        the same PAID-or-WAIVED "settled" rule PaymentCreditService.
        _update_order_status already uses to decide Order.status == COMPLETED.
        """
        installments = self.installments.all()
        if self.access_policy == PaymentPlan.AccessPolicy.FIRST_INSTALLMENT:
            return installments.filter(status=Installment.Status.PAID).exists()
        if self.access_policy == PaymentPlan.AccessPolicy.FULL_PAYMENT:
            return installments.exists() and not installments.exclude(
                status__in=[Installment.Status.PAID, Installment.Status.WAIVED]
            ).exists()
        return False


class Installment(TimeStampedModel):
    """
    One expected portion of an Order, generated deterministically from the
    Order's frozen snapshot at creation time — see
    OrderService._create_installments() in services.py. Never created any
    other way.
    """

    class Status(models.TextChoices):
        SCHEDULED = "SCHEDULED", "Scheduled"
        DUE = "DUE", "Due"
        PENDING = "PENDING", "Pending"
        PAID = "PAID", "Paid"
        FAILED = "FAILED", "Failed"
        WAIVED = "WAIVED", "Waived"
        CANCELLED = "CANCELLED", "Cancelled"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="installments")
    sequence = models.PositiveIntegerField(help_text="1-based position within the order's installment schedule.")
    expected_amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)
    due_date = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED, db_index=True)

    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["order", "sequence"]
        constraints = [
            models.UniqueConstraint(fields=["order", "sequence"], name="unique_installment_sequence_per_order"),
            models.CheckConstraint(condition=models.Q(sequence__gt=0), name="installment_sequence_positive"),
            models.CheckConstraint(condition=models.Q(expected_amount__gt=0), name="installment_amount_positive"),
            models.CheckConstraint(
                condition=models.Q(status__in=["SCHEDULED", "DUE", "PENDING", "PAID", "FAILED", "WAIVED", "CANCELLED"]),
                name="installment_status_valid",
            ),
        ]
        permissions = [
            ("cancel_installment", "Can cancel an eligible unpaid installment (Phase 8 administrative action)"),
            ("waive_installment", "Can waive an eligible unpaid installment (Phase 8 administrative action)"),
        ]

    def __str__(self):
        return f"{self.order_id} #{self.sequence} ({self.status})"

    def clean(self):
        """
        Defense-in-depth only — per the project brief, financial invariants
        must not rely solely on Model.clean(). The DB-level constraints above
        (positive amount, positive sequence, unique sequence-per-order, valid
        status) are the primary enforcement; OrderService is the only path
        that creates installments and is responsible for correct sequencing
        and not exceeding order.installment_count in the first place.
        """
        if self.order_id and self.sequence and self.sequence > self.order.installment_count:
            raise ValidationError({"sequence": "Sequence cannot exceed the order's snapshotted installment count."})


def generate_tara_product_id() -> str:
    """
    Non-sensitive, opaque identifier for a single PaymentAttempt — carries no
    customer name/email/phone or course name, per the project brief. Derived
    from a random UUID4, not from any PII or sequential/guessable value.
    """
    return f"vcrm-{uuid.uuid4().hex}"


class PaymentAttempt(TimeStampedModel):
    """
    One provider (Tara) interaction for one Installment. An installment may
    have multiple attempts (e.g. retry after FAILED/EXPIRED) — each gets its
    own unique tara_product_id, never reused.

    Deliberately created nowhere in Phase 3 — see
    docs/TARA_INTEGRATION_PROJECT.md Phase 3 report for why "create a first
    PaymentAttempt" was judged out of Phase 3's persistence-only scope
    (creating one implies immediately wanting to call POST /tara/paymentlinks,
    which Phase 3 explicitly excludes). Phase 5 (Checkout) is responsible for
    creating the first PaymentAttempt for an order's first payable Installment.
    """

    class Status(models.TextChoices):
        CREATED = "CREATED", "Created"
        LINK_CREATED = "LINK_CREATED", "Link Created"
        PENDING = "PENDING", "Pending"
        SUCCEEDED = "SUCCEEDED", "Succeeded"
        FAILED = "FAILED", "Failed"
        EXPIRED = "EXPIRED", "Expired"
        UNKNOWN = "UNKNOWN", "Unknown"

    installment = models.ForeignKey(
        Installment, on_delete=models.PROTECT, related_name="payment_attempts",
        help_text="PROTECTed, not CASCADEd — a payment attempt is provider-interaction history and must never be silently deleted.",
    )
    reference = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True)

    tara_product_id = models.CharField(max_length=255, unique=True, default=generate_tara_product_id)
    tara_payment_id = models.CharField(
        max_length=255, null=True, blank=True, unique=True,
        help_text="Tara's own payment identifier, once known. NULL until the provider assigns one; unique when set "
                   "(Postgres unique indexes permit multiple NULLs, so this doesn't block multiple not-yet-known attempts).",
    )

    expected_amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CREATED, db_index=True)
    raw_provider_status = models.CharField(
        max_length=50, blank=True,
        help_text="Tara's raw status string, stored uninterpreted — never itself used for business decisions; status above is.",
    )
    provider_message = models.CharField(max_length=500, blank=True)

    # Provider-returned payment links (docs/Tara_API_Reference_Technique.docx
    # §3) — bounded, typed fields matching the documented response shape, not
    # an unbounded raw payload blob.
    whatsapp_link = models.URLField(blank=True)
    telegram_link = models.URLField(blank=True)
    dikalo_link = models.URLField(blank=True)
    general_link = models.URLField(blank=True)
    card_link = models.URLField(blank=True)
    sms_link = models.CharField(
        max_length=500, blank=True,
        help_text="An sms: URI, not an http(s) URL per Tara's documented response — plain CharField, "
                   "since URLField only validates http(s)/ftp schemes.",
    )

    initiated_at = models.DateTimeField(null=True, blank=True, help_text="When a payment link was actually created (status -> LINK_CREATED).")
    completed_at = models.DateTimeField(null=True, blank=True, help_text="When a terminal status (SUCCEEDED/FAILED/EXPIRED) was reached.")
    expires_at = models.DateTimeField(null=True, blank=True, help_text="Link expiry, once Tara documents/returns one. Unknown/unpopulated in Phase 3.")

    # Phase 9 (docs/TARA_INTEGRATION_PROJECT.md) — bounded hourly-reconciliation
    # bookkeeping only; never read by any business/state-transition rule.
    reconciliation_check_count = models.PositiveIntegerField(
        default=0, help_text="How many times run_hourly_reconciliation has verified this attempt via check_transaction_status().",
    )
    next_reconciliation_check_at = models.DateTimeField(
        null=True, blank=True, help_text="Backoff — not re-selected for verification before this time.",
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=models.Q(expected_amount__gt=0), name="payment_attempt_amount_positive"),
            models.CheckConstraint(
                condition=models.Q(status__in=["CREATED", "LINK_CREATED", "PENDING", "SUCCEEDED", "FAILED", "EXPIRED", "UNKNOWN"]),
                name="payment_attempt_status_valid",
            ),
            # Phase 5: at most one non-terminal-retryable attempt per
            # installment at a time — FAILED/EXPIRED are excluded because a
            # fresh attempt replacing one of those is exactly the documented
            # "an installment may have multiple payment attempts" case.
            # Enforced here as a DB-level backstop in addition to
            # CheckoutService's row-locked get-or-create (apps/payments/services.py).
            models.UniqueConstraint(
                fields=["installment"],
                condition=models.Q(status__in=["CREATED", "LINK_CREATED", "PENDING", "SUCCEEDED", "UNKNOWN"]),
                name="one_active_payment_attempt_per_installment",
            ),
        ]
        permissions = [
            ("check_tara_status", "Can trigger a server-to-server Tara status check for a payment attempt"),
        ]

    def __str__(self):
        return f"{self.tara_product_id} ({self.status})"


class TaraWebhookEvent(TimeStampedModel):
    """
    Immutable record of an inbound Tara webhook delivery (Phase 6,
    docs/TARA_INTEGRATION_PROJECT.md). Critical trust constraint: Tara's
    documentation does not define a signature header, algorithm, signed byte
    sequence, encoding, timestamp, or replay window — see
    integrations/payments/tara/signature.py's docstring. Every event recorded
    here was, by construction, UNVERIFIED at receipt time
    (verification_mode=UNVERIFIED_TRIGGER); it is only ever used as a trigger
    to call TaraClient.check_transaction_status() — see
    WebhookProcessingService in services.py. No field here is ever treated as
    proof of payment on its own.

    Deliberately does NOT store phoneNumber or any raw request body — only
    bounded, normalized correlation fields plus a payload digest for
    deduplication/audit. No API key or webhook secret is ever stored here.
    """

    class ProcessingStatus(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        PROCESSED = "PROCESSED", "Processed"
        UNCORRELATED = "UNCORRELATED", "Uncorrelated"
        REJECTED = "REJECTED", "Rejected"
        FAILED = "FAILED", "Failed"

    class VerificationMode(models.TextChoices):
        UNVERIFIED_TRIGGER = "UNVERIFIED_TRIGGER", "Unverified — used only as a trigger"
        SERVER_TO_SERVER = "SERVER_TO_SERVER", "Server-to-server status verified"

    class VerificationResult(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SUCCESS = "SUCCESS", "Success"
        FAILURE = "FAILURE", "Failure"
        UNKNOWN = "UNKNOWN", "Unknown"
        SKIPPED = "SKIPPED", "Skipped"

    class FailureCategory(models.TextChoices):
        NONE = "", "—"
        BUSINESS_ID_MISMATCH = "BUSINESS_ID_MISMATCH", "businessId mismatch"
        UNKNOWN_PRODUCT_ID = "UNKNOWN_PRODUCT_ID", "Unknown productId"
        UNCORRELATED = "UNCORRELATED", "No safe correlation available"
        AMOUNT_MISMATCH = "AMOUNT_MISMATCH", "Webhook amount does not match the expected installment amount"
        PAYMENT_ID_CONFLICT = "PAYMENT_ID_CONFLICT", "Tara paymentId already linked to a different attempt"
        PROVIDER_LOOKUP_INDETERMINATE = "PROVIDER_LOOKUP_INDETERMINATE", "Server-to-server status lookup was indeterminate"
        MALFORMED = "MALFORMED", "Malformed or inconsistent provider response"

    reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    processing_status = models.CharField(max_length=20, choices=ProcessingStatus.choices, default=ProcessingStatus.RECEIVED, db_index=True)
    verification_mode = models.CharField(max_length=30, choices=VerificationMode.choices, default=VerificationMode.UNVERIFIED_TRIGGER)
    verification_result = models.CharField(max_length=10, choices=VerificationResult.choices, default=VerificationResult.PENDING)
    failure_category = models.CharField(max_length=40, choices=FailureCategory.choices, blank=True, default="")

    business_id = models.CharField(max_length=255, blank=True)
    tara_product_id = models.CharField(max_length=255, blank=True, db_index=True)
    tara_payment_id = models.CharField(max_length=255, blank=True, db_index=True)
    raw_provider_status = models.CharField(max_length=50, blank=True, help_text="Tara's raw status string as received, uninterpreted.")

    provider_creation_date = models.DateTimeField(null=True, blank=True)
    provider_change_date = models.DateTimeField(null=True, blank=True)

    payload_digest = models.CharField(max_length=64, db_index=True, help_text="SHA-256 hex digest of the raw request body. The body itself is never stored.")
    dedup_key = models.CharField(
        max_length=64, unique=True, db_index=True,
        help_text="SHA-256 hex digest of paymentId|productId|status|changeDate|payload_digest — deterministic deduplication.",
    )

    payment_attempt = models.ForeignKey(
        PaymentAttempt, null=True, blank=True, on_delete=models.SET_NULL, related_name="webhook_events",
        help_text="Set only via an exact tara_product_id match. Never set from phoneNumber/amount/collectionId.",
    )

    processing_attempts = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-received_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(processing_status__in=["RECEIVED", "PROCESSED", "UNCORRELATED", "REJECTED", "FAILED"]),
                name="webhook_event_processing_status_valid",
            ),
        ]

    def __str__(self):
        return f"WebhookEvent {self.reference} ({self.processing_status})"


class PaymentConfirmation(TimeStampedModel):
    """
    Durable, worker-processed customer notification for one newly-verified
    successful Installment (Phase 7, docs/TARA_INTEGRATION_PROJECT.md).
    Created inside the same atomic transaction as PaymentCreditService.
    apply_verified_success()'s state transitions — never as a best-effort
    in-memory callback — so a process crash right after commit can never lose
    a confirmation that needs sending. A separate worker/management command
    (PaymentConfirmationDeliveryService, apps/payments/management/commands/
    process_payment_confirmations.py) claims and sends it afterward, outside
    any payment transaction.

    Deliberately stores no API keys, webhook secret, Tara raw payload, full
    payment URL, internal exception text, or database primary keys — only
    bounded, safe fields plus an opaque UUID reference. recipient_snapshot
    freezes the destination address at creation time so a later Contact.email
    edit can't change where a not-yet-sent confirmation goes, and so a sent
    confirmation's audit record stays accurate regardless of later edits.
    """

    class NotificationType(models.TextChoices):
        PAYMENT_CONFIRMATION = "PAYMENT_CONFIRMATION", "Payment confirmation"

    class Channel(models.TextChoices):
        EMAIL = "EMAIL", "Email"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SENDING = "SENDING", "Sending"
        SENT = "SENT", "Sent"
        FAILED = "FAILED", "Failed"
        MANUAL_REVIEW = "MANUAL_REVIEW", "Manual Review"

    class FailureCategory(models.TextChoices):
        NONE = "", "—"
        INVALID_RECIPIENT = "INVALID_RECIPIENT", "Missing or invalid recipient address"
        TRANSIENT_DELIVERY_ERROR = "TRANSIENT_DELIVERY_ERROR", "Transient delivery error"
        CONFIGURATION_ERROR = "CONFIGURATION_ERROR", "Delivery configuration error"
        MAX_ATTEMPTS_EXCEEDED = "MAX_ATTEMPTS_EXCEEDED", "Maximum delivery attempts exceeded"

    reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    notification_type = models.CharField(
        max_length=30, choices=NotificationType.choices, default=NotificationType.PAYMENT_CONFIRMATION,
    )

    contact = models.ForeignKey("contacts.Contact", on_delete=models.PROTECT, related_name="payment_confirmations")
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="payment_confirmations")
    installment = models.ForeignKey(Installment, on_delete=models.PROTECT, related_name="payment_confirmations")
    payment_attempt = models.ForeignKey(PaymentAttempt, on_delete=models.PROTECT, related_name="payment_confirmations")

    channel = models.CharField(max_length=10, choices=Channel.choices, default=Channel.EMAIL)
    recipient_snapshot = models.CharField(
        max_length=255, blank=True,
        help_text="Recipient address frozen at creation time — never re-read from the live Contact at send time.",
    )

    idempotency_key = models.CharField(
        max_length=64, unique=True, db_index=True,
        help_text="SHA-256 hex of notification_type|payment_attempt_id|channel|recipient — never a mutable provider message.",
    )

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    attempt_count = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_failure_category = models.CharField(max_length=40, choices=FailureCategory.choices, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "next_attempt_at"])]
        permissions = [
            ("retry_payment_confirmation", "Can retry a FAILED or MANUAL_REVIEW payment confirmation"),
        ]

    def __str__(self):
        return f"Confirmation {self.reference} ({self.status})"


class AdminAuditLog(TimeStampedModel):
    """
    Immutable audit trail for sensitive administrative/manual actions (Phase
    8, docs/TARA_INTEGRATION_PROJECT.md). Django's own admin.LogEntry is not
    used for this — it has no mandatory-reason field, no before/after
    business-state pair, and (being generic-relation-based, deletable via
    contenttypes cascades) is not immutable by construction the way this
    model is kept immutable purely through admin registration (no
    add/change/delete permission — see PaymentConfirmationAdmin's sibling
    AdminAuditLogAdmin).

    Written in the SAME database transaction as the state change it records
    (see apps/payments/admin_services.py::AdminAuditService.record(), called
    from inside each administrative service method's own atomic block) — an
    audit record and its corresponding state change either both commit or
    both roll back together. A safe audit record is also written for
    rejected/no-op attempts (e.g. invalid state, insufficient permission)
    where practical, without recording sensitive details.

    Deliberately stores no API keys, webhook secret, provider payload,
    payment links, decrypted credentials, raw external error text, or a full
    model serialization — `previous_state`/`resulting_state` are short safe
    strings (e.g. a status value, optionally with a safe category), never a
    dump of the object. `administrator_username` is a frozen snapshot (the
    FK is nullable/SET_NULL) so the record stays meaningful even if the user
    account is later deleted.
    """

    class ActionType(models.TextChoices):
        CHECK_TARA_STATUS = "CHECK_TARA_STATUS", "Check Tara Status"
        RETRY_CONFIRMATION = "RETRY_CONFIRMATION", "Retry Confirmation"
        RETRY_PROVISIONING = "RETRY_PROVISIONING", "Retry Provisioning"
        CANCEL_ORDER = "CANCEL_ORDER", "Cancel Order"
        CANCEL_INSTALLMENT = "CANCEL_INSTALLMENT", "Cancel Installment"
        WAIVE_INSTALLMENT = "WAIVE_INSTALLMENT", "Waive Installment"
        APPLY_MANUAL_DISPOSITION = "APPLY_MANUAL_DISPOSITION", "Apply Manual Disposition"
        FREEZE_ENROLLMENT = "FREEZE_ENROLLMENT", "Freeze Enrollment"
        RESUME_ENROLLMENT = "RESUME_ENROLLMENT", "Resume Enrollment"

    class TargetType(models.TextChoices):
        ORDER = "ORDER", "Order"
        INSTALLMENT = "INSTALLMENT", "Installment"
        PAYMENT_ATTEMPT = "PAYMENT_ATTEMPT", "PaymentAttempt"
        PAYMENT_CONFIRMATION = "PAYMENT_CONFIRMATION", "PaymentConfirmation"
        PROVISIONING_REQUEST = "PROVISIONING_REQUEST", "ProvisioningRequest"
        ENROLLMENT_ATTEMPT = "ENROLLMENT_ATTEMPT", "EnrollmentAttempt"

    class OutcomeCategory(models.TextChoices):
        SUCCESS = "SUCCESS", "Success"
        NO_OP_ALREADY_IN_STATE = "NO_OP_ALREADY_IN_STATE", "No-op — already in target state"
        REJECTED_INVALID_STATE = "REJECTED_INVALID_STATE", "Rejected — invalid state for this action"
        REJECTED_PERMISSION = "REJECTED_PERMISSION", "Rejected — insufficient permission"
        PROVIDER_NON_FINAL = "PROVIDER_NON_FINAL", "Provider result non-final (pending/unknown/timeout/error)"
        FAILED = "FAILED", "Failed"

    reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)

    administrator = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="admin_audit_logs",
    )
    administrator_username = models.CharField(max_length=150, blank=True)

    action_type = models.CharField(max_length=40, choices=ActionType.choices, db_index=True)
    target_type = models.CharField(max_length=30, choices=TargetType.choices, db_index=True)
    target_reference = models.CharField(
        max_length=255,
        help_text="Safe opaque reference to the target (e.g. Order.reference) — never a bare internal pk where a safer identifier exists.",
    )

    order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.SET_NULL, related_name="admin_audit_logs")
    installment = models.ForeignKey(Installment, null=True, blank=True, on_delete=models.SET_NULL, related_name="admin_audit_logs")
    payment_attempt = models.ForeignKey(PaymentAttempt, null=True, blank=True, on_delete=models.SET_NULL, related_name="admin_audit_logs")

    previous_state = models.CharField(max_length=255, blank=True)
    resulting_state = models.CharField(max_length=255, blank=True)
    reason = models.TextField(help_text="Mandatory administrator-supplied reason for this action.")
    outcome_category = models.CharField(max_length=40, choices=OutcomeCategory.choices, db_index=True)

    request_metadata = models.JSONField(
        default=dict, blank=True,
        help_text="Safe metadata only (e.g. remote_addr) — never headers, cookies, or tokens.",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Admin Audit Log"
        verbose_name_plural = "Admin Audit Logs"

    def __str__(self):
        return f"{self.action_type} on {self.target_type} {self.target_reference} ({self.outcome_category})"
