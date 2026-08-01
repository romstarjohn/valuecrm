from django import forms
from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.core.exceptions import PermissionDenied, ValidationError
from django.template.response import TemplateResponse
from django.utils import timezone

from apps.provisioning.models import ProvisioningRequest

from .admin_actions import confirmed_admin_action
from .admin_services import (
    AdminActionError,
    OrderAdministrationService,
    PaymentAttemptAdministrationService,
    PaymentConfirmationAdministrationService,
)
from .models import (
    AdminAuditLog,
    Installment,
    Order,
    Payment,
    PaymentAttempt,
    PaymentConfirmation,
    PaymentPlan,
    PaymentTimelineEvent,
    ReconciliationRun,
    TaraConfig,
    TaraWebhookEvent,
)
from .services import PaymentMatchingService, TaraConfigService


class TaraConfigForm(forms.ModelForm):
    """
    api_key/webhook_secret are declared here but deliberately listed in
    Meta.exclude below rather than Meta.fields. A ModelForm field assigned via
    Meta.fields is populated onto the instance by Django's construct_instance()
    during super().save() — for a write-only PasswordInput(render_value=False)
    field left blank on every normal edit (it never pre-populates), that would
    silently overwrite the existing encrypted value with an empty string before
    TaraConfigService.update_credentials() ever runs.

    Meta.exclude (not Meta.fields) is required here specifically because this
    form is used through Django Admin: ModelAdmin.get_form() recomputes its own
    `fields` list from the admin's fieldsets and passes it explicitly to
    modelform_factory(), which silently overrides a plain Meta.fields on the
    base form — but Django deliberately merges a custom form's Meta.exclude
    into the admin's exclude list when the ModelAdmin itself defines no
    `exclude` (see django.contrib.admin.options.ModelAdmin.get_form). Do not
    switch this back to Meta.fields, and do not add these two fields to
    TaraConfigAdmin.exclude either (that would remove them from the rendered
    form entirely, not just from construct_instance()).
    """
    api_key = forms.CharField(
        widget=forms.PasswordInput(render_value=False),
        required=False,
        help_text="Leave blank to keep the current key. Authenticates OUTBOUND calls TO Tara. Never pre-filled or redisplayed.",
    )
    webhook_secret = forms.CharField(
        widget=forms.PasswordInput(render_value=False),
        required=False,
        help_text="Leave blank to keep the current secret. Verifies INBOUND webhook calls FROM Tara — "
                   "a different secret than the API key above. Never pre-filled or redisplayed.",
    )

    class Meta:
        model = TaraConfig
        exclude = ["api_key", "webhook_secret"]

    def validate_unique(self):
        """
        Skip Django's automatic pre-save uniqueness check for is_active against
        TaraConfig.Meta's unique_active_tara_config constraint. Activating a new
        configuration is meant to atomically deactivate whichever one was active
        before it — that's TaraConfig.save()'s job, not a validation error to
        reject. The DB-level constraint stays in effect as a defense-in-depth
        guard against code paths that bypass save() entirely (e.g. a raw
        .update() call) — see tests/services/test_tara_config_service.py
        ::test_db_level_constraint_blocks_two_active_rows_even_bypassing_save.
        """
        exclude = self._get_validation_exclusions()
        exclude.add("is_active")
        try:
            self.instance.validate_unique(exclude=exclude)
        except ValidationError as e:
            self._update_errors(e)

    def validate_constraints(self):
        """
        Django 6.0 checks Meta.constraints (UniqueConstraint) via this separate
        method, not validate_unique() above — both must exclude is_active for
        the same reason. See validate_unique()'s docstring.
        """
        exclude = self._get_validation_exclusions()
        exclude.add("is_active")
        try:
            self.instance.validate_constraints(exclude=exclude)
        except ValidationError as e:
            self._update_errors(e)

    def save(self, commit=True):
        instance = super().save(commit=False)
        TaraConfigService().update_credentials(
            instance,
            self.cleaned_data.get("api_key"),
            self.cleaned_data.get("webhook_secret"),
        )
        if commit:
            instance.save()
        return instance


@admin.register(TaraConfig)
class TaraConfigAdmin(admin.ModelAdmin):
    """
    No credential-verification action is registered here. Tara documents no
    safe, read-only endpoint to check a key/businessId against (Phase 0
    discovery), and the prior action called a fabricated `GET {BASE_URL}/account`
    that does not exist in Tara's documented API surface — see
    apps/payments/services.py::TaraConfigService docstring. Every configuration
    stays validation_status=PENDING (unverified) until a later phase confirms a
    real check. Do not re-add a verification action without that confirmation.
    """
    form = TaraConfigForm
    list_display = (
        "name", "business_id", "is_active", "api_key_configured",
        "webhook_secret_configured", "validation_status", "updated_at",
    )
    list_filter = ("is_active", "validation_status")
    readonly_fields = ("validation_status", "api_key_configured", "webhook_secret_configured", "raw_payload")

    @admin.display(description="API Key", boolean=True)
    def api_key_configured(self, obj):
        """Presence-only indicator — never the decrypted value or any fragment of it."""
        return bool(obj.api_key)

    @admin.display(description="Webhook Secret", boolean=True)
    def webhook_secret_configured(self, obj):
        """Presence-only indicator — never the decrypted value or any fragment of it."""
        return bool(obj.webhook_secret)


class PaymentTimelineEventInline(admin.TabularInline):
    """Read-only chronological timeline — see this Payment's full match/provisioning history."""
    model = PaymentTimelineEvent
    extra = 0
    fields = ("created_at", "event_type", "detail")
    readonly_fields = ("created_at", "event_type", "detail")
    can_delete = False
    ordering = ("created_at",)

    def has_add_permission(self, request, obj=None):
        return False


class ProvisioningRequestInline(admin.TabularInline):
    """Read-only: shows the provisioning side of this payment's timeline, spanning both lifecycles on one page."""
    model = ProvisioningRequest
    fk_name = "payment"
    extra = 0
    fields = ("product", "contact", "status", "policy_snapshot", "attempt_count", "last_error", "updated_at")
    readonly_fields = ("product", "contact", "status", "policy_snapshot", "attempt_count", "last_error", "updated_at")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "provider_transaction_id", "product_ref", "status", "match_confidence",
        "matched_contact", "matched_product", "created_at",
    )
    list_filter = ("status", "match_confidence", "provider")
    search_fields = ("provider_transaction_id", "product_ref", "extracted_email", "extracted_phone")
    raw_id_fields = ("matched_contact", "matched_product")
    readonly_fields = (
        "provider", "provider_transaction_id", "raw_payload", "product_ref",
        "extracted_phone", "extracted_email", "amount", "currency",
        "status", "match_confidence", "verified_at", "created_at", "updated_at",
    )
    inlines = [PaymentTimelineEventInline, ProvisioningRequestInline]
    actions = ["retry_matching_action", "dismiss_action"]

    @admin.action(description="Retry Matching")
    def retry_matching_action(self, request, queryset):
        from apps.provisioning.services import ProvisioningService

        matching_service = PaymentMatchingService()
        provisioning_service = ProvisioningService()
        processed = 0
        for payment in queryset:
            matching_service.match_and_process(payment)
            if payment.status == Payment.Status.MATCHED:
                provisioning_service.create_request_from_payment(payment)
            processed += 1
        self.message_user(request, f"Re-ran matching for {processed} payment(s).")

    @admin.action(description="Dismiss (mark Ignored)")
    def dismiss_action(self, request, queryset):
        matching_service = PaymentMatchingService()
        count = 0
        for payment in queryset.filter(status=Payment.Status.NEEDS_REVIEW):
            matching_service.dismiss(payment, notes=f"Dismissed by {request.user} via admin action.")
            count += 1
        self.message_user(request, f"Marked {count} payment(s) as ignored.")

    def save_model(self, request, obj, form, change):
        """
        Editing matched_contact directly on a NEEDS_REVIEW payment is the
        "Link to Contact" operator action. If a product is already matched too,
        this manual link is treated as an operator-confirmed HIGH-confidence
        identity match and immediately proceeds to provisioning, same as an
        automatic match would.
        """
        manually_linked = (
            change and "matched_contact" in form.changed_data and obj.matched_contact_id
            and obj.status == Payment.Status.NEEDS_REVIEW
        )
        super().save_model(request, obj, form, change)

        if manually_linked and obj.matched_product_id:
            obj.match_confidence = Payment.Confidence.HIGH
            obj.status = Payment.Status.MATCHED
            obj.review_notes = (obj.review_notes + f"\nManually linked to contact by {request.user} on {timezone.now()}").strip()
            obj.save()
            PaymentTimelineEvent.objects.create(
                payment=obj,
                event_type=PaymentTimelineEvent.EventType.CUSTOMER_MATCHED,
                detail={"manual": True, "linked_by": str(request.user)},
            )
            from apps.provisioning.services import ProvisioningService

            ProvisioningService().create_request_from_payment(obj)
            self.message_user(request, f"Payment manually linked to {obj.matched_contact} and marked MATCHED.")


@admin.register(ReconciliationRun)
class ReconciliationRunAdmin(admin.ModelAdmin):
    """Read-only, per Phase 3. Phase 9 adds the hourly-run safe counters — see ReconciliationRun's docstring."""
    list_display = (
        "started_at", "completed_at", "run_status", "attempts_examined", "status_checks",
        "verified_successes", "verified_failures", "still_pending_or_unknown",
        "missing_work_repaired", "safe_error_count",
    )
    list_filter = ("run_status",)
    readonly_fields = [f.name for f in ReconciliationRun._meta.get_fields() if hasattr(f, "attname")]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PaymentPlan)
class PaymentPlanAdmin(admin.ModelAdmin):
    list_display = (
        "name", "code", "course", "currency", "installment_count",
        "installment_amount", "computed_total_display", "access_policy",
        "is_active", "display_order", "updated_at",
    )
    list_editable = ("is_active", "display_order")
    list_filter = ("is_active", "currency", "access_policy")
    search_fields = ("code", "name", "course__name")
    autocomplete_fields = ("course",)
    readonly_fields = ("computed_total_display",)

    @admin.display(description="Computed Total")
    def computed_total_display(self, obj):
        if not obj.pk:
            return "— (save the plan to compute)"
        return f"{obj.computed_total:,.2f} {obj.currency}"

    def has_delete_permission(self, request, obj=None):
        """
        Deletion is intentionally never exposed here — the brief's admin action
        list for Phase 1 is create/view/edit/activate/deactivate/reorder, not
        delete; retiring a plan means deactivating it. Once apps.payments.Order
        exists (Phase 3) with Order.plan on_delete=PROTECT, deleting a plan with
        historical orders will already be impossible at the DB level too.
        """
        return False


class InstallmentInline(admin.TabularInline):
    """Read-only — see OrderAdmin docstring. Installments are generated deterministically by OrderService, never edited by hand."""
    model = Installment
    extra = 0
    fields = ("sequence", "expected_amount", "currency", "due_date", "status", "paid_amount", "paid_at")
    readonly_fields = fields
    can_delete = False
    ordering = ("sequence",)

    def has_add_permission(self, request, obj=None):
        return False


class PaymentConfirmationInline(admin.TabularInline):
    """
    Read-only (Phase 7) — never displays recipient_snapshot's raw value
    beyond what's already safe to show an admin (an email address, not a
    secret), and never displays rendered email body/provider payload (there
    is none stored on this model at all — see PaymentConfirmation's
    docstring).
    """
    model = PaymentConfirmation
    fk_name = "order"
    extra = 0
    fields = ("reference", "installment", "status", "attempt_count", "last_failure_category", "sent_at")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class OrderProvisioningRequestInline(admin.TabularInline):
    """Read-only — the Phase 7 (Order/course) enrollment work record(s) for this order, if any."""

    model = ProvisioningRequest
    fk_name = "order"
    extra = 0
    fields = ("course", "status", "failure_category", "attempt_count", "updated_at")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class OrderAdminAuditLogInline(admin.TabularInline):
    """Read-only — the administrative audit trail for this order (Phase 8)."""
    model = AdminAuditLog
    fk_name = "order"
    extra = 0
    fields = ("created_at", "action_type", "administrator_username", "previous_state", "resulting_state", "outcome_category", "reason")
    readonly_fields = fields
    can_delete = False
    ordering = ("-created_at",)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    """
    Record fields stay read-only (Phase 3 decision, unchanged) — every
    business-state change happens only through the explicit, permission-gated,
    audited actions below (Phase 8), never by hand-editing a field. Orders/
    installments are created only by OrderService.create_order() — see
    apps/payments/services.py.
    """
    list_display = (
        "reference", "customer", "plan_name", "course_name", "status",
        "manual_disposition", "currency", "total_expected_amount",
        "total_verified_paid_display", "remaining_balance_display",
        "installment_progress_display", "access_eligible_display",
        "confirmation_state_display", "provisioning_state_display",
        "created_at", "updated_at",
    )
    list_filter = ("status", "currency", "access_policy", "manual_disposition")
    search_fields = ("reference", "idempotency_key", "customer__email", "plan_code", "course_name")
    readonly_fields = [f.name for f in Order._meta.get_fields() if hasattr(f, "attname")]
    inlines = [InstallmentInline, PaymentConfirmationInline, OrderProvisioningRequestInline, OrderAdminAuditLogInline]
    actions = ["cancel_order_action", "apply_manual_disposition_action"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        """
        select_related the live FKs shown/searched on every row; prefetch the
        reverse relations the display methods below iterate in Python
        (installments/confirmations/provisioning requests) so a changelist
        page of N orders doesn't issue O(N) extra queries for them.
        """
        return (
            super().get_queryset(request)
            .select_related("customer", "plan", "course")
            .prefetch_related("installments", "payment_confirmations", "provisioning_requests")
        )

    @admin.display(description="Total Verified Paid")
    def total_verified_paid_display(self, obj):
        """
        Computed from the prefetched installments list (not
        Order.total_paid_amount, which re-queries via .aggregate() and would
        reintroduce an N+1 on this listing) — same PAID-only rule.
        """
        total = sum((i.paid_amount or 0) for i in obj.installments.all() if i.status == Installment.Status.PAID)
        return f"{total:,.2f}"

    @admin.display(description="Remaining Balance")
    def remaining_balance_display(self, obj):
        paid = sum((i.paid_amount or 0) for i in obj.installments.all() if i.status == Installment.Status.PAID)
        return f"{obj.total_expected_amount - paid:,.2f}"

    @admin.display(description="Installments")
    def installment_progress_display(self, obj):
        installments = list(obj.installments.all())
        paid = sum(1 for i in installments if i.status == Installment.Status.PAID)
        return f"{paid}/{obj.installment_count}"

    @admin.display(description="Access Eligible", boolean=True)
    def access_eligible_display(self, obj):
        """
        Same rule as Order.is_access_eligible, computed from the prefetched
        installments list instead of a live .filter()/.exclude() query, for
        the same N+1-avoidance reason as total_verified_paid_display.
        """
        installments = list(obj.installments.all())
        if obj.access_policy == PaymentPlan.AccessPolicy.FIRST_INSTALLMENT:
            return any(i.status == Installment.Status.PAID for i in installments)
        if obj.access_policy == PaymentPlan.AccessPolicy.FULL_PAYMENT:
            return bool(installments) and all(i.status in (Installment.Status.PAID, Installment.Status.WAIVED) for i in installments)
        return False

    @admin.display(description="Confirmation State")
    def confirmation_state_display(self, obj):
        statuses = {c.status for c in obj.payment_confirmations.all()}
        return ", ".join(sorted(statuses)) if statuses else "—"

    @admin.display(description="Provisioning State")
    def provisioning_state_display(self, obj):
        statuses = {r.status for r in obj.provisioning_requests.all()}
        return ", ".join(sorted(statuses)) if statuses else "—"

    @confirmed_admin_action(description="Cancel Order", permission="payments.cancel_order")
    def cancel_order_action(self, request, obj, reason):
        try:
            OrderAdministrationService().cancel_order(obj.pk, reason, request.user, request)
        except AdminActionError as e:
            return str(e), messages.ERROR
        return f"Order {obj.reference} cancelled.", messages.SUCCESS

    def apply_manual_disposition_action(self, request, queryset):
        """
        Bespoke (not confirmed_admin_action) because this action needs an
        extra field — the target disposition value — beyond the generic
        reason textarea; same permission-check/confirmation-page/POST-only/
        mandatory-reason shape as every other Phase 8 action otherwise.
        """
        if not request.user.has_perm("payments.apply_manual_disposition"):
            raise PermissionDenied

        if "confirm_apply" in request.POST:
            reason = request.POST.get("reason", "").strip()
            disposition = request.POST.get("disposition", "")
            if not reason:
                self.message_user(request, "A reason is required for this action.", level=messages.ERROR)
            else:
                service = OrderAdministrationService()
                count = 0
                for obj in queryset:
                    try:
                        service.apply_manual_disposition(obj.pk, disposition, reason, request.user, request)
                        count += 1
                    except AdminActionError as e:
                        self.message_user(request, str(e), level=messages.ERROR)
                if count:
                    self.message_user(request, f"Applied disposition to {count} order(s).", level=messages.SUCCESS)
            return None

        return TemplateResponse(request, "admin/payments/confirm_disposition_action.html", {
            **self.admin_site.each_context(request),
            "title": "Apply Manual Disposition",
            "description": "Apply Manual Disposition",
            "objects": queryset,
            "opts": self.model._meta,
            "action_checkbox_name": ACTION_CHECKBOX_NAME,
            "action_name": "apply_manual_disposition_action",
            "dispositions": Order.ManualDisposition.choices,
        })

    apply_manual_disposition_action.short_description = "Apply Manual Disposition"


@admin.register(PaymentAttempt)
class PaymentAttemptAdmin(admin.ModelAdmin):
    """
    Record fields stay read-only (Phase 3 decision, unchanged) — provider
    fields (raw_provider_status, tara_product_id, tara_payment_id) are never
    editable and are displayed as clearly separate columns from the internal
    `status` (Phase 8). The only sanctioned way to change status here is the
    Check Tara Status action below, which only ever applies a genuine
    server-to-server provider result via PaymentCreditService — never
    administrator input.
    """
    list_display = (
        "tara_product_id", "tara_payment_id", "status", "raw_provider_status",
        "needs_review_display", "installment", "expected_amount", "currency",
        "initiated_at", "completed_at", "created_at",
    )
    list_filter = ("status",)
    search_fields = ("tara_product_id", "tara_payment_id", "reference")
    readonly_fields = [f.name for f in PaymentAttempt._meta.get_fields() if hasattr(f, "attname")]
    actions = ["check_tara_status_action"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Needs Review", boolean=True)
    def needs_review_display(self, obj):
        return obj.status in (PaymentAttempt.Status.PENDING, PaymentAttempt.Status.UNKNOWN)

    @confirmed_admin_action(description="Check Tara Status", permission="payments.check_tara_status")
    def check_tara_status_action(self, request, obj, reason):
        try:
            attempt = PaymentAttemptAdministrationService().check_tara_status(obj.pk, reason, request.user, request)
        except AdminActionError as e:
            return str(e), messages.ERROR
        return f"Tara status checked for {attempt.tara_product_id}: attempt is now {attempt.status}.", messages.SUCCESS


@admin.register(TaraWebhookEvent)
class TaraWebhookEventAdmin(admin.ModelAdmin):
    """
    Read-only, per Phase 6. Displays only safe fields — this model never
    stores a raw payload, phone number, payment URL, or provider secret in the
    first place (see TaraWebhookEvent's docstring), so there is nothing
    unsafe to accidentally expose here regardless of field selection, but the
    explicit list below matches exactly what the project brief allows.
    """
    list_display = (
        "reference", "received_at", "processing_status", "verification_mode",
        "verification_result", "failure_category", "payment_attempt",
    )
    list_filter = ("processing_status", "verification_mode", "verification_result", "failure_category")
    search_fields = ("reference", "tara_product_id", "tara_payment_id")
    readonly_fields = [f.name for f in TaraWebhookEvent._meta.get_fields() if hasattr(f, "attname")]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PaymentConfirmation)
class PaymentConfirmationAdmin(admin.ModelAdmin):
    """
    Read-only, per Phase 7. This model never stores a rendered email body,
    provider payload, API key, or payment URL in the first place (see
    PaymentConfirmation's docstring) — recipient_snapshot is the only
    customer-identifying field, and it's just the destination email address,
    not sensitive content.
    """
    list_display = (
        "reference", "order", "installment", "channel", "status",
        "attempt_count", "last_failure_category", "sent_at",
    )
    list_filter = ("status", "channel", "last_failure_category")
    search_fields = ("reference", "order__reference", "contact__email")
    readonly_fields = [f.name for f in PaymentConfirmation._meta.get_fields() if hasattr(f, "attname")]
    actions = ["retry_confirmation_action"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @confirmed_admin_action(description="Retry Confirmation", permission="payments.retry_payment_confirmation")
    def retry_confirmation_action(self, request, obj, reason):
        try:
            PaymentConfirmationAdministrationService().retry_confirmation(obj.pk, reason, request.user, request)
        except AdminActionError as e:
            return str(e), messages.ERROR
        return f"Confirmation {obj.reference} queued for retry.", messages.SUCCESS


@admin.register(Installment)
class InstallmentAdmin(admin.ModelAdmin):
    """
    Top-level registration (Phase 8) alongside the existing read-only
    InstallmentInline on OrderAdmin — needed because Django Admin actions are
    a changelist mechanism, not available on inlines. Record fields stay
    read-only; Cancel/Waive are the only sanctioned ways to change status
    here, both permission-gated, reason-required, and audited (see
    apps/payments/admin_services.py::OrderAdministrationService).
    """
    list_display = ("order", "sequence", "status", "expected_amount", "currency", "due_date", "paid_amount", "paid_at")
    list_filter = ("status", "currency")
    search_fields = ("order__reference", "order__customer__email")
    readonly_fields = [f.name for f in Installment._meta.get_fields() if hasattr(f, "attname")]
    actions = ["cancel_installment_action", "waive_installment_action"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("order", "order__customer")

    @confirmed_admin_action(description="Cancel Installment", permission="payments.cancel_installment")
    def cancel_installment_action(self, request, obj, reason):
        try:
            OrderAdministrationService().cancel_installment(obj.pk, reason, request.user, request)
        except AdminActionError as e:
            return str(e), messages.ERROR
        return f"Installment {obj} cancelled.", messages.SUCCESS

    @confirmed_admin_action(description="Waive Installment", permission="payments.waive_installment")
    def waive_installment_action(self, request, obj, reason):
        try:
            OrderAdministrationService().waive_installment(obj.pk, reason, request.user, request)
        except AdminActionError as e:
            return str(e), messages.ERROR
        return f"Installment {obj} waived.", messages.SUCCESS


@admin.register(AdminAuditLog)
class AdminAuditLogAdmin(admin.ModelAdmin):
    """
    Immutable, per Phase 8 — no add/change/delete permission at all. The
    only way a row is created is AdminAuditService.record(), called from
    inside the same transaction as the state change it documents.
    """
    list_display = (
        "reference", "created_at", "action_type", "target_type", "target_reference",
        "administrator_username", "outcome_category", "previous_state", "resulting_state",
    )
    list_filter = ("action_type", "target_type", "outcome_category")
    search_fields = ("reference", "target_reference", "administrator_username", "order__reference")
    readonly_fields = [f.name for f in AdminAuditLog._meta.get_fields() if hasattr(f, "attname")]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
