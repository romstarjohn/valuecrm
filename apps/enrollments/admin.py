from django.contrib import admin
from .models import EnrollmentAttempt

@admin.register(EnrollmentAttempt)
class EnrollmentAttemptAdmin(admin.ModelAdmin):
    list_display = ("contact", "course", "status", "cf_enrollment_id", "created_at")
    list_filter = ("status", "course", "created_at")
    search_fields = ("contact__email", "course__name", "cf_enrollment_id")
    readonly_fields = (
        "contact", 
        "course", 
        "status", 
        "cf_enrollment_id", 
        "error_log", 
        "response_payload", 
        "created_at", 
        "updated_at"
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
