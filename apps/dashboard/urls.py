from django.urls import path
from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.index, name="index"),
    path("ui/table-demo/", views.table_demo, name="table_demo"),
]
