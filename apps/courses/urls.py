from django.urls import path
from . import views

app_name = "courses"

urlpatterns = [
    path("", views.course_list, name="list"),
    path("sync/", views.course_sync, name="sync"),
    path("<str:cf_course_id>/", views.course_detail, name="detail"),
]
