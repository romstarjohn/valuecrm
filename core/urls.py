from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from ninja import NinjaAPI
from ninja.security import django_auth

from apps.configuration.api import router as configuration_router
from apps.contacts.api import router as contacts_router
from apps.courses.api import router as courses_router
from apps.enrollments.api import router as enrollments_router
from apps.payments.api import router as payments_router

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
    path("", include("apps.dashboard.urls")),
    path("contacts/", include("apps.contacts.urls")),
    path("courses/", include("apps.courses.urls")),
    path("enrollments/", include("apps.enrollments.urls")),
    path("settings/", include("apps.configuration.urls")),
    path("operations/", include("apps.operations.urls")),
    # Public guest checkout (Phase 5) — the only non-staff-facing pages in
    # this app; deliberately not under the staff portal's sidebar/topbar.
    path("checkout/", include("apps.payments.urls")),
    path("admin/", admin.site.urls),
    path("api/", api.urls),
]
