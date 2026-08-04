from django.contrib import admin, messages

from apps.payments.admin_actions import confirmed_admin_action

from .models import ProvisioningAttempt, ProvisioningRequest
from .services import ProvisioningError, ProvisioningService


class ProvisioningAttemptInline(admin.TabularInline):
    model = ProvisioningAttempt
    extra = 0
    fields = ("course", "enrollment_attempt", "status", "created_at")
    readonly_fields = ("course", "enrollment_attempt", "status", "created_at")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ProvisioningRequest)
class ProvisioningRequestAdmin(admin.ModelAdmin):
    """
    Read-only work-record fields plus a small set of explicit operator
    actions — no field here is ever hand-edited. Never displays ClickFunnels
    credentials, raw provider response, or webhook payloads — only safe
    identifiers/status fields plus order/course/failure_category.
    """
    list_display = (
        "order", "course", "contact",
        "status", "failure_category", "attempt_count", "updated_at",
    )
    list_filter = ("status", "failure_category", "policy_snapshot")
    search_fields = (
        "contact__email", "order__reference", "course__name",
    )
    readonly_fields = (
        "order", "course", "contact", "policy_snapshot",
        "attempt_count", "last_error", "failure_category", "approved_by", "approved_at",
    )
    inlines = [ProvisioningAttemptInline]
    actions = ["provision_now_action", "approve_action", "retry_action", "cancel_action"]

    @admin.action(description="Provision Now")
    def provision_now_action(self, request, queryset):
        service = ProvisioningService()
        count = 0
        for req in queryset.filter(status=ProvisioningRequest.Status.PENDING):
            service.execute(req)
            count += 1
        self.message_user(request, f"Executed {count} request(s).")

    @admin.action(description="Approve")
    def approve_action(self, request, queryset):
        service = ProvisioningService()
        count = 0
        for req in queryset.filter(status=ProvisioningRequest.Status.AWAITING_APPROVAL):
            service.approve(req, request.user)
            count += 1
        self.message_user(request, f"Approved {count} request(s) — now in the Manual/Pending queue.")

    @confirmed_admin_action(description="Retry", permission="provisioning.retry_provisioning_request")
    def retry_action(self, request, obj, reason):
        """
        Hardened for Phase 8 (docs/TARA_INTEGRATION_PROJECT.md): permission-
        gated, reason-required, intermediate-confirmation-page, and — unlike
        the Phase 7 version — no longer calls execute()/ClickFunnels
        synchronously inside the admin request. ProvisioningService.
        retry_request() rejects COMPLETED requests, revalidates Order
        eligibility for the new flow, resets the request to PENDING (claimable
        by the existing worker, process_pending_provisioning), and writes an
        audit record — all in one transaction. Includes MANUAL_REVIEW as well
        as FAILED — an explicit human action is always a legitimate way to
        retry a non-retryable-category failure once an operator has fixed the
        underlying cause; only the automatic worker skips MANUAL_REVIEW.
        """
        try:
            ProvisioningService().retry_request(obj.pk, reason, request.user, request)
        except ProvisioningError as e:
            return str(e), messages.ERROR
        return f"Provisioning request #{obj.pk} queued for retry.", messages.SUCCESS

    @admin.action(description="Cancel")
    def cancel_action(self, request, queryset):
        service = ProvisioningService()
        count = 0
        excluded = [ProvisioningRequest.Status.COMPLETED, ProvisioningRequest.Status.CANCELLED]
        for req in queryset.exclude(status__in=excluded):
            service.cancel(req)
            count += 1
        self.message_user(request, f"Cancelled {count} request(s).")
