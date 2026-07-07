from django.contrib import admin, messages
from django import forms
from .models import ClickFunnelsConfig
from .services import ConfigurationService
from integrations.clickfunnels.client import ClickFunnelsClient

class ClickFunnelsConfigForm(forms.ModelForm):
    # Masked fields: never sent back to browser with actual data
    api_access_token = forms.CharField(
        widget=forms.PasswordInput(render_value=False), 
        required=False,
        help_text="Leave blank to keep current token."
    )

    class Meta:
        model = ClickFunnelsConfig
        fields = ["name", "is_active", "api_user_agent", "api_access_token"]

    def save(self, commit=True):
        instance = super().save(commit=False)
        service = ConfigurationService()
        # Use service to handle encryption and model update
        service.update_credentials(
            instance, 
            self.cleaned_data.get("api_access_token")
        )
        if commit:
            instance.save()
        return instance

@admin.register(ClickFunnelsConfig)
class ClickFunnelsConfigAdmin(admin.ModelAdmin):
    form = ClickFunnelsConfigForm
    list_display = ("name", "is_active", "validation_status", "workspace_name", "updated_at")
    list_filter = ("is_active", "validation_status")
    readonly_fields = ("validation_status", "workspace_id", "workspace_name", "team_id", "team_name", "business_info", "raw_payload")
    actions = ["verify_credentials_action"]

    @admin.action(description="Verify ClickFunnels Credentials")
    def verify_credentials_action(self, request, queryset):
        success_count = 0
        
        for config in queryset:
            try:
                # Factory method builds the client with the decrypted token
                client = ClickFunnelsClient.from_configuration(config)
                service = ConfigurationService(client=client)
                service.verify_credentials(config)
                success_count += 1
            except Exception as e:
                self.message_user(request, f"Error verifying {config.name}: {str(e)}", level=messages.ERROR)
        
        if success_count:
            self.message_user(request, f"Successfully verified {success_count} configurations.")
