from django.urls import path
from . import views

app_name = "contacts"

urlpatterns = [
    path("", views.contact_list, name="list"),
    path("add/", views.contact_create, name="add"),
    path("<int:pk>/", views.contact_detail, name="detail"),
    path("<int:pk>/edit/", views.contact_update, name="edit"),
]
