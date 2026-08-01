"""
Shared "confirmed admin action" pattern (Phase 8, docs/TARA_INTEGRATION_PROJECT.md).

No pre-existing repository convention for a permission-gated, reason-required,
intermediate-confirmation-page admin action existed before this phase (Phase 0
discovery confirmed no LogEntry usage, no custom Meta.permissions, no
custom-action confirmation template anywhere in the repo). This establishes
one, modeled directly on Django's own built-in `delete_selected` action /
`admin/delete_selected_confirmation.html` (same two-phase POST flow, same
hidden-field structure, same admin chrome) so every sensitive Phase 8 action
looks and behaves consistently with stock Django Admin. Reused by both
apps.payments.admin and apps.provisioning.admin.
"""
from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.core.exceptions import PermissionDenied
from django.template.response import TemplateResponse

CONFIRM_APPLY_FIELD = "confirm_apply"
CONFIRM_TEMPLATE = "admin/payments/confirm_action.html"


def confirmed_admin_action(*, description: str, permission: str):
    """
    Factory for a Django Admin action that requires: an explicit server-side
    permission check, a mandatory reason, an intermediate confirmation page,
    and POST-only execution — never a state change from a bare GET, and never
    triggered by an unauthorized user (the permission check runs first, before
    the target queryset is touched at all, so a rejected request causes no DB
    mutation and no external call).

    `handler(self, request, obj, reason) -> (message_text, message_level)`
    is called once per selected object only after permission + confirmation +
    a non-empty reason all pass. It must reload/lock the target itself (via
    the administrative service methods in apps/payments/admin_services.py and
    apps/provisioning/services.py::ProvisioningService.retry_request — never
    trust the admin changelist's in-memory `obj`) — this decorator passes
    `obj` only so the handler can read its primary key and, for the
    confirmation page, `obj` is also used read-only to render a safe summary.
    A handler that raises any Exception has its message shown as an admin
    error and causes no further processing for that object.
    """
    def decorator(handler):
        def action(self, request, queryset):
            if not request.user.has_perm(permission):
                raise PermissionDenied

            if CONFIRM_APPLY_FIELD in request.POST:
                reason = request.POST.get("reason", "").strip()
                if not reason:
                    self.message_user(request, "A reason is required for this action.", level=messages.ERROR)
                    return _render_confirmation(self, request, queryset, action.__name__, description, reason="")

                for obj in queryset:
                    try:
                        text, level = handler(self, request, obj, reason)
                        self.message_user(request, text, level=level)
                    except Exception as e:
                        self.message_user(request, str(e), level=messages.ERROR)
                return None

            return _render_confirmation(self, request, queryset, action.__name__, description, reason="")

        action.__name__ = handler.__name__
        action.short_description = description
        return admin.action(description=description)(action)

    return decorator


def _render_confirmation(model_admin, request, queryset, action_name, description, reason):
    context = {
        **model_admin.admin_site.each_context(request),
        "title": description,
        "description": description,
        "objects": queryset,
        "opts": model_admin.model._meta,
        "action_checkbox_name": ACTION_CHECKBOX_NAME,
        "action_name": action_name,
        "reason": reason,
    }
    return TemplateResponse(request, CONFIRM_TEMPLATE, context)
