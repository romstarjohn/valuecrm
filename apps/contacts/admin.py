from django.contrib import admin
from .models import Contact

@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("email", "first_name", "last_name", "status", "source", "cf_contact_id", "created_at")
    search_fields = ("email", "first_name", "last_name", "cf_contact_id")
    list_filter = ("status", "source", "created_at")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {
            "fields": ("email", "first_name", "last_name", "phone", "cf_contact_id")
        }),
        ("Details", {
            "fields": ("status", "time_zone", "source", "tags")
        }),
        ("Addresses", {
            "fields": ("shipping_address", "billing_address")
        }),
        ("Placeholders", {
            "fields": ("payment_method_placeholder", "order_information_placeholder")
        }),
        ("Metadata", {
            "fields": ("raw_payload", "created_at", "updated_at")
        }),
    )
