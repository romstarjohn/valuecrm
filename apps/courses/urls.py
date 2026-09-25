from django.urls import path
from . import views

app_name = "courses"

urlpatterns = [
    path("", views.course_list, name="list"),
    path("sync/", views.course_sync, name="sync"),
    path("products/", views.product_list, name="product_list"),
    path("products/add/", views.product_create, name="product_add"),
    path("products/<int:pk>/edit/", views.product_update, name="product_edit"),
    path("<str:cf_course_id>/panel/", views.course_detail_panel, name="detail_panel"),
    path("<str:cf_course_id>/", views.course_detail, name="detail"),
]
