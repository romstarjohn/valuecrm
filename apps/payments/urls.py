from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("", views.shop_index, name="shop_index"),
    path("status/<str:token>/", views.checkout_status, name="checkout_status"),
    path("status/<str:token>/refresh/", views.checkout_status_refresh, name="checkout_status_refresh"),
    path("<slug:slug>/", views.checkout_start, name="checkout_start"),
]
