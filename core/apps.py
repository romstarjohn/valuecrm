from django.contrib.admin.apps import AdminConfig


class RestrictedAdminConfig(AdminConfig):
    """Swaps in shared.admin_site.SuperuserOnlyAdminSite as the default admin
    site — see that module's docstring. Referenced from INSTALLED_APPS in
    place of the plain "django.contrib.admin"."""

    default_site = "shared.admin_site.SuperuserOnlyAdminSite"
