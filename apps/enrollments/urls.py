from django.urls import path
from . import views

app_name = "enrollments"

urlpatterns = [
    path("", views.enrollment_list, name="list"),
    path("new/", views.enrollment_new, name="new"),
    path("bulk/", views.enrollment_bulk, name="bulk"),
    path("<int:pk>/panel/", views.enrollment_detail_panel, name="detail_panel"),
    path("<int:pk>/", views.enrollment_detail, name="detail"),
    path("<int:pk>/freeze/", views.enrollment_freeze, name="freeze"),
    path("<int:pk>/resume/", views.enrollment_resume, name="resume"),
]
