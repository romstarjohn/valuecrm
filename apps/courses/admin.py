from django.contrib import admin, messages
from .models import Course
from .services import CourseService
from apps.configuration.services import ConfigurationService
from integrations.clickfunnels.client import ClickFunnelsClient

@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("name", "cf_course_id", "workspace_id", "created_at")
    search_fields = ("name", "cf_course_id")
    list_filter = ("workspace_id", "created_at")
    readonly_fields = ("cf_course_id", "workspace_id", "raw_payload", "created_at", "updated_at")
    actions = ["sync_courses_action"]

    @admin.action(description="Sync Courses From ClickFunnels")
    def sync_courses_action(self, request, queryset):
        config_service = ConfigurationService()
        config = config_service.get_active_config()
        
        if not config:
            self.message_user(request, "No active configuration found.", level=messages.ERROR)
            return

        if not config.workspace_id:
            self.message_user(request, "Select a workspace before syncing courses.", level=messages.ERROR)
            return

        if not config.workspace_subdomain:
            self.message_user(request, "Workspace subdomain is missing. Workspace-level API calls cannot continue.", level=messages.ERROR)
            return

        try:
            client = ClickFunnelsClient.from_configuration(config)
            service = CourseService(client=client)
            result = service.sync_courses(
                workspace_id=int(config.workspace_id),
                workspace_subdomain=config.workspace_subdomain
            )
            self.message_user(
                request, 
                f"Successfully synced {result['synced_count']} courses "
                f"({result['created_count']} created, {result['updated_count']} updated)."
            )
        except Exception as e:
            self.message_user(request, f"Error syncing courses: {str(e)}", level=messages.ERROR)
