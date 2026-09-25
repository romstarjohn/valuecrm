from django.contrib.admin import AdminSite
from django.contrib.admin.forms import AdminAuthenticationForm
from django.core.exceptions import ValidationError


class SuperuserAuthenticationForm(AdminAuthenticationForm):
    """
    Same as Django's own admin login form, plus: staff-but-not-superuser is
    rejected at login time with a clear message, rather than being allowed to
    authenticate and only then hitting a permission wall on /admin/ itself.
    See AGENT.md — team members use /login/ (templates/registration/login.html);
    only is_superuser accounts use /admin/login/.
    """

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.is_superuser:
            raise ValidationError(
                "Ce compte n'a pas accès à l'administration. "
                "Utilisez la page de connexion de l'équipe.",
                code="not_superuser",
            )


class SuperuserOnlyAdminSite(AdminSite):
    """
    Restricts the whole Django Admin backend (not just the login form) to
    is_superuser accounts — see AGENT.md for the access-model decision this
    implements. Registered as the default admin site via
    core.apps.RestrictedAdminConfig.default_site, so every app's existing
    `@admin.register(...)` / `admin.site.register(...)` call is unaffected.
    """

    login_form = SuperuserAuthenticationForm

    def has_permission(self, request):
        return request.user.is_active and request.user.is_superuser
