from django.db import models
from shared.models import TimeStampedModel

class Contact(TimeStampedModel):
    first_name = models.CharField(max_length=255, blank=True)
    last_name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=50, blank=True)
    
    tags = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=50, blank=True)
    time_zone = models.CharField(max_length=100, blank=True)
    source = models.CharField(max_length=255, blank=True)
    
    shipping_address = models.JSONField(default=dict, blank=True)
    billing_address = models.JSONField(default=dict, blank=True)
    
    payment_method_placeholder = models.CharField(max_length=255, blank=True)
    order_information_placeholder = models.CharField(max_length=255, blank=True)
    
    cf_contact_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.email})".strip()

    class Meta:
        ordering = ["-created_at"]
