from django.urls import path
from . import views

app_name = "configuration"

urlpatterns = [
    path("settings/", views.settings_view, name="settings"),
    path("settings/verify/", views.verify_connection, name="verify"),
    path("tara/", views.tara_settings_view, name="tara_settings"),
]
