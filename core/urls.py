from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from ninja import NinjaAPI
from ninja.security import django_auth

from apps.configuration.api import router as configuration_router
from apps.contacts.api import router as contacts_router
from apps.courses.api import router as courses_router
from apps.enrollments.api import router as enrollments_router

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

urlpatterns = [
    path("", include("apps.dashboard.urls")),
    path("contacts/", include("apps.contacts.urls")),
    path("courses/", include("apps.courses.urls")),
    path("enrollments/", include("apps.enrollments.urls")),
    path("settings/", include("apps.configuration.urls")),
    path("admin/", admin.site.urls),
    path("api/", api.urls),
]
