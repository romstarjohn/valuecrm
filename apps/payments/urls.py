from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("", views.plan_list, name="plan_list"),
    path("<int:plan_id>/", views.checkout_start, name="checkout_start"),
    path("status/<str:token>/", views.checkout_status, name="checkout_status"),
]
