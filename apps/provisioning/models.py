from django.conf import settings
from django.db import models

from shared.models import TimeStampedModel


class Product(TimeStampedModel):
    """
    Internal catalog entry. The chain is:
    Tara product/reference -> ProductMapping -> Product -> ClickFunnels course(s).
    """

    class ProvisioningPolicy(models.TextChoices):
        AUTOMATIC = "AUTOMATIC", "Automatic"
        MANUAL = "MANUAL", "Manual"
        SCHEDULED = "SCHEDULED", "Scheduled (nightly batch)"
        APPROVAL_REQUIRED = "APPROVAL_REQUIRED", "Approval required"

    name = models.CharField(max_length=255)
    courses = models.ManyToManyField("courses.Course", related_name="products", blank=True)
    provisioning_policy = models.CharField(
        max_length=30, choices=ProvisioningPolicy.choices, default=ProvisioningPolicy.MANUAL
    )
    expected_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Reference amount used only as a weak corroborating signal during payment matching "
                   "— never sufficient on its own to trigger provisioning.",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ProductMapping(TimeStampedModel):
    provider = models.CharField(max_length=50, default="tara")
    external_ref = models.CharField(
        max_length=255,
        help_text="The exact product/reference identifier as sent by the provider "
                   "(e.g. Tara's product/plan reference) — copy this verbatim from an "
                   "unresolved Payment's product_ref, not a display name.",
    )
    product = models.ForeignKey(Product, related_name="mappings", on_delete=models.CASCADE)

    class Meta:
        unique_together = ("provider", "external_ref")
        ordering = ["provider", "external_ref"]

    def __str__(self):
        return f"{self.provider}:{self.external_ref} -> {self.product.name}"


class ProvisioningRequest(TimeStampedModel):
    """
    Provisioning lifecycle only — separate from Payment's lifecycle by design.

    Two coexisting origins, mutually exclusive (see Meta.constraints):

    - Legacy: created once a (legacy) Payment reaches MATCHED with HIGH
      confidence — payment/product set, order/course NULL. Course(s) to
      enroll come from product.courses (M2M).
    - New (Phase 7, docs/TARA_INTEGRATION_PROJECT.md): created when an Order
      (apps.payments) becomes access-eligible after a verified installment —
      order/course set, payment/product NULL. The single course to enroll is
      the Order's frozen course snapshot, never the live PaymentPlan. See
      ProvisioningService.create_request_from_order().

    Uniqueness for the new flow is (contact, course), not (contact, order) —
    a second Order for a course the contact is already being/was provisioned
    for must not create a duplicate course-membership request; order is kept
    only for audit traceability (see unique_active_contact_course_
    provisioning_request below).
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SCHEDULED = "SCHEDULED", "Scheduled"
        AWAITING_APPROVAL = "AWAITING_APPROVAL", "Awaiting Approval"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"
        MANUAL_REVIEW = "MANUAL_REVIEW", "Manual Review"
        CANCELLED = "CANCELLED", "Cancelled"

    class FailureCategory(models.TextChoices):
        NONE = "", "—"
        TRANSIENT_PROVIDER_ERROR = "TRANSIENT_PROVIDER_ERROR", "Transient provider error (timeout/5xx/rate limit)"
        MISSING_CONTACT_IDENTIFIER = "MISSING_CONTACT_IDENTIFIER", "Missing contact email/identifier"
        MISSING_COURSE_SNAPSHOT = "MISSING_COURSE_SNAPSHOT", "Missing course_cf_id snapshot"
        CONFIGURATION_ERROR = "CONFIGURATION_ERROR", "Invalid local configuration"
        AUTHORIZATION_ERROR = "AUTHORIZATION_ERROR", "Authorization failure requiring admin action"
        MALFORMED_REQUEST = "MALFORMED_REQUEST", "Malformed permanent request"
        MAX_ATTEMPTS_EXCEEDED = "MAX_ATTEMPTS_EXCEEDED", "Maximum attempts exceeded"

    payment = models.ForeignKey(
        "payments.Payment", null=True, blank=True, on_delete=models.PROTECT, related_name="provisioning_requests",
    )
    product = models.ForeignKey(
        Product, null=True, blank=True, on_delete=models.PROTECT, related_name="provisioning_requests",
    )
    order = models.ForeignKey(
        "payments.Order", null=True, blank=True, on_delete=models.PROTECT, related_name="provisioning_requests",
        help_text="New-flow origin (Phase 7) — audit link only. Never read for course/policy identity; course below is authoritative.",
    )
    course = models.ForeignKey(
        "courses.Course", null=True, blank=True, on_delete=models.PROTECT, related_name="provisioning_requests",
        help_text="New-flow origin (Phase 7) only — the single course to enroll, from the Order's frozen snapshot.",
    )
    contact = models.ForeignKey("contacts.Contact", on_delete=models.PROTECT, related_name="provisioning_requests")
    # Policy at creation time — later edits to Product.provisioning_policy never
    # retroactively change in-flight requests.
    policy_snapshot = models.CharField(max_length=30)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    approved_at = models.DateTimeField(null=True, blank=True)
    attempt_count = models.IntegerField(default=0)
    last_error = models.TextField(blank=True)
    failure_category = models.CharField(max_length=40, choices=FailureCategory.choices, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "created_at"])]
        constraints = [
            models.CheckConstraint(
                condition=(
                    (models.Q(payment__isnull=False) & models.Q(product__isnull=False)
                     & models.Q(order__isnull=True) & models.Q(course__isnull=True))
                    | (models.Q(payment__isnull=True) & models.Q(product__isnull=True)
                       & models.Q(order__isnull=False) & models.Q(course__isnull=False))
                ),
                name="provisioning_request_exactly_one_origin",
            ),
            models.UniqueConstraint(
                fields=["contact", "course"],
                condition=models.Q(course__isnull=False) & ~models.Q(status="CANCELLED"),
                name="unique_active_contact_course_provisioning_request",
            ),
        ]
        permissions = [
            ("retry_provisioning_request", "Can retry a FAILED or MANUAL_REVIEW provisioning request (Phase 8)"),
        ]

    def __str__(self):
        return f"{self.contact} -> {self.product or self.course} ({self.status})"


class ProvisioningAttempt(TimeStampedModel):
    """
    One row per course-enrollment try within a ProvisioningRequest. Deliberately
    reuses EnrollmentAttempt (which already stores the full CF response payload)
    instead of duplicating that data — this just links a request to the one-or-
    many EnrollmentAttempt rows it produced.
    """

    class Status(models.TextChoices):
        SUCCESS = "SUCCESS", "Success"
        FAILURE = "FAILURE", "Failure"

    provisioning_request = models.ForeignKey(ProvisioningRequest, related_name="attempts", on_delete=models.CASCADE)
    course = models.ForeignKey("courses.Course", on_delete=models.PROTECT)
    enrollment_attempt = models.ForeignKey(
        "enrollments.EnrollmentAttempt", null=True, blank=True, on_delete=models.SET_NULL
    )
    status = models.CharField(max_length=20, choices=Status.choices)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.provisioning_request_id} - {self.course} ({self.status})"
