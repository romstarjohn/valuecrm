from django.db import models
from shared.models import TimeStampedModel

class ClickFunnelsConfig(TimeStampedModel):
    class ValidationStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        VALID = "VALID", "Valid"
        INVALID = "INVALID", "Invalid"

    name = models.CharField(max_length=255, help_text="A descriptive name for this configuration.")
    is_active = models.BooleanField(default=False, db_index=True, help_text="Set to true if this is the active configuration.")
    
    # Stores encrypted API Access Token. Encryption/Decryption handled by ConfigurationService.
    api_access_token = models.TextField(blank=True)
    api_user_agent = models.CharField(max_length=255, default="ValuedCRM/1.0", help_text="Mandatory User-Agent identification for CFv2.")
    
    validation_status = models.CharField(
        max_length=20, 
        choices=ValidationStatus.choices, 
        default=ValidationStatus.PENDING,
        db_index=True
    )
    
    workspace_id = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    workspace_name = models.CharField(max_length=255, blank=True, null=True)
    workspace_subdomain = models.CharField(max_length=255, blank=True, null=True, help_text="The subdomain for workspace-level API calls.")
    
    team_id = models.CharField(max_length=255, blank=True, null=True)
    team_name = models.CharField(max_length=255, blank=True, null=True)
    
    business_info = models.JSONField(default=dict, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "ClickFunnels Configuration"
        verbose_name_plural = "ClickFunnels Configurations"

    def __str__(self):
        return f"{self.name} ({self.validation_status})"

    def save(self, *args, **kwargs):
        if self.is_active:
            ClickFunnelsConfig.objects.filter(is_active=True).exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)


class NotificationSettings(TimeStampedModel):
    """
    Where the team is told about things that need a human (the "À traiter"
    inbox), edited under Réglages → Notifications. A single row — use
    NotificationSettings.current(). Only exceptions are sent, never routine sales.
    """
    alert_email = models.EmailField(
        blank=True,
        help_text="Adresse qui reçoit les alertes « À traiter ». Laisser vide pour ne rien envoyer.",
    )
    alerts_enabled = models.BooleanField(default=True)

    @classmethod
    def current(cls) -> "NotificationSettings":
        settings_row = cls.objects.order_by("pk").first()
        return settings_row or cls.objects.create(alert_email="contact@digital-mind.tech")

    def __str__(self):
        return self.alert_email or "(aucune adresse)"


class StaffAlert(TimeStampedModel):
    """
    One "À traiter" item the team has already been e-mailed about — the unique
    `key` (e.g. "access:12") is what keeps the hourly digest from repeating itself.
    """
    key = models.CharField(max_length=100, unique=True)
    summary = models.CharField(max_length=500)

    def __str__(self):
        return self.key
