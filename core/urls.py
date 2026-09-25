from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include
from django.conf import settings
from ninja import NinjaAPI
from ninja.security import django_auth

from apps.configuration.api import router as configuration_router
from apps.contacts.api import router as contacts_router
from apps.courses.api import router as courses_router
from apps.enrollments.api import router as enrollments_router
from apps.payments.api import router as payments_router
from shared.auth import RateLimitedLoginView
from shared.forms import BootstrapPasswordChangeForm

api = NinjaAPI(
    title="ValuedCRM API",
    auth=django_auth,  # Require valid Django session for all endpoints
    docs_url="/docs" if settings.DEBUG else None
)

@api.get("/health", tags=["System"])
def health_check(request):
    return {"status": "ok"}

api.add_router("/config", configuration_router)
api.add_router("/contacts", contacts_router)
api.add_router("/courses", courses_router)
api.add_router("/enrollments", enrollments_router)
# /tara/webhook/ overrides auth=None at the operation level (Phase 6) — see
# apps/payments/api.py::tara_webhook's docstring for why this route trusts
# nothing about the request and returns only generic responses.
api.add_router("/tara", payments_router)

urlpatterns = [
    # Portal sign-in/out for team members — see shared/admin_site.py and
    # AGENT.md. /admin/login/ (below, via admin.site.urls) is superuser-only.
    # URL names (login/logout/password_change/password_change_done) stay in
    # English — every {% url %} reference and Django's own next_page="login"
    # resolution use the name, not the path — only the visible path is French,
    # matching /paiement/'s precedent.
    path("connexion/", RateLimitedLoginView.as_view(
        template_name="registration/login.html", redirect_authenticated_user=True,
    ), name="login"),
    path("deconnexion/", auth_views.LogoutView.as_view(next_page="login"), name="logout"),
    # Portal-side password change — deliberately NOT /admin/password_change/,
    # which shared/admin_site.py's has_permission() now blocks for anyone
    # who isn't a superuser (see AGENT.md).
    path("compte/mot-de-passe/", auth_views.PasswordChangeView.as_view(
        template_name="registration/password_change.html", success_url="/compte/mot-de-passe/confirmation/",
        form_class=BootstrapPasswordChangeForm,
    ), name="password_change"),
    path("compte/mot-de-passe/confirmation/", auth_views.PasswordChangeDoneView.as_view(
        template_name="registration/password_change_done.html",
    ), name="password_change_done"),
    path("", include("apps.dashboard.urls")),
    path("contacts/", include("apps.contacts.urls")),
    path("courses/", include("apps.courses.urls")),
    path("enrollments/", include("apps.enrollments.urls")),
    path("plans/", include("apps.payments.staff_urls")),
    path("settings/", include("apps.configuration.urls")),
    path("operations/", include("apps.operations.urls")),
    # Public guest checkout (Phase 5) — the only non-staff-facing pages in
    # this app; deliberately not under the staff portal's sidebar/topbar.
    # French-facing URL: /paiement/ (shop of products) and /paiement/<slug>/
    # (one product's checkout, slug = its CheckoutOffer.slug).
    path("paiement/", include("apps.payments.urls")),
    path("admin/", admin.site.urls),
    path("api/", api.urls),
]

handler403 = "shared.error_views.permission_denied"
