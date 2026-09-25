from django.urls import path

from . import staff_views

app_name = "plans"

urlpatterns = [
    path("", staff_views.plan_list, name="list"),
    path("add/", staff_views.plan_create, name="add"),
    path("<int:pk>/edit/", staff_views.plan_update, name="edit"),
]
