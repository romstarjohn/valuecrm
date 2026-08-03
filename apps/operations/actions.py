"""
Dashboard-native sibling of apps/payments/admin_actions.py::confirmed_admin_action
(Django Admin, Phase 8) — same permission-check -> mandatory-reason ->
intermediate confirmation page -> POST-only-execution shape, using the
custom dashboard's own base.html chrome instead of admin chrome (the admin
version can't be reused directly: it depends on ModelAdmin machinery —
self.model._meta, admin_urlname, ACTION_CHECKBOX_NAME — none of which exists
here). This helper only handles shared web-request plumbing; every action
view still calls the exact same administrative service methods
(OrderAdministrationService, PaymentAttemptAdministrationService,
PaymentConfirmationAdministrationService, ProvisioningService.retry_request)
Phase 8 already established — no state-transition logic is duplicated here.
"""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import render

CONFIRM_TEMPLATE = "operations/confirm_action.html"


def render_confirmation_or_process(request, *, permission, context, service_call):
    """
    `service_call(reason) -> str` performs the action (via the existing
    service layer, which reloads/locks the target server-side and writes the
    audit record itself) and returns a safe success message, or raises
    AdminActionError/ProvisioningError with a safe message.

    Returns an HttpResponse to send back to the browser (the confirmation
    page, on first GET/POST-without-confirm, or after a validation error), or
    None to signal the caller should redirect after a processed POST.
    """
    if not request.user.has_perm(permission):
        raise PermissionDenied

    if request.method == "POST" and "confirm_apply" in request.POST:
        reason = request.POST.get("reason", "").strip()
        if not reason:
            messages.error(request, "A reason is required for this action.")
            return render(request, CONFIRM_TEMPLATE, {**context, "reason": ""})

        from apps.payments.admin_services import AdminActionError
        from apps.provisioning.services import ProvisioningError

        try:
            message = service_call(reason)
            messages.success(request, message)
        except (AdminActionError, ProvisioningError) as e:
            messages.error(request, str(e))
        return None

    return render(request, CONFIRM_TEMPLATE, context)
