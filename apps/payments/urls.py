from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("", views.checkout_start, name="checkout_start"),
    path("status/<str:token>/", views.checkout_status, name="checkout_status"),
]
