from django.conf import settings
from django.db import models

from shared.models import TimeStampedModel


class ProvisioningRequest(TimeStampedModel):
    """
    Provisioning lifecycle only — separate from Payment's lifecycle by design.

    Created when an Order (apps.payments) becomes access-eligible after a
    verified installment (docs/TARA_INTEGRATION_PROJECT.md, Phase 7). The
    single course to enroll is the Order's frozen course snapshot, never the
    live PaymentPlan. See ProvisioningService.create_request_from_order().

    Uniqueness is (contact, course), not (contact, order) — a second Order
    for a course the contact is already being/was provisioned for must not
    create a duplicate course-membership request; order is kept only for
    audit traceability (see unique_active_contact_course_provisioning_request
    below).
    """

    class PolicySnapshot(models.TextChoices):
        AUTOMATIC = "AUTOMATIC", "Automatic"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
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

    order = models.ForeignKey(
        "payments.Order", on_delete=models.PROTECT, related_name="provisioning_requests",
        help_text="Audit link only. Never read for course/policy identity; course below is authoritative.",
    )
    course = models.ForeignKey(
        "courses.Course", on_delete=models.PROTECT, related_name="provisioning_requests",
        help_text="The single course to enroll, from the Order's frozen snapshot.",
    )
    contact = models.ForeignKey("contacts.Contact", on_delete=models.PROTECT, related_name="provisioning_requests")
    # Snapshotted at creation time — always PolicySnapshot.AUTOMATIC today, but
    # kept as a field (rather than inlined) so a future non-automatic policy
    # doesn't require a schema change.
    policy_snapshot = models.CharField(max_length=30, choices=PolicySnapshot.choices)

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
            models.UniqueConstraint(
                fields=["contact", "course"],
                condition=~models.Q(status="CANCELLED"),
                name="unique_active_contact_course_provisioning_request",
            ),
        ]
        permissions = [
            ("retry_provisioning_request", "Can retry a FAILED or MANUAL_REVIEW provisioning request (Phase 8)"),
        ]

    def __str__(self):
        return f"{self.contact} -> {self.course} ({self.status})"


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
