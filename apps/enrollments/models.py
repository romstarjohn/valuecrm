from django.db import models
from shared.models import TimeStampedModel
from apps.contacts.models import Contact
from apps.courses.models import Course

class EnrollmentAttempt(TimeStampedModel):
    class Status(models.TextChoices):
        SUCCESS = "SUCCESS", "Success"
        FAILURE = "FAILURE", "Failure"

    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="enrollment_attempts")
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="enrollment_attempts")
    
    status = models.CharField(max_length=20, choices=Status.choices, db_index=True)
    cf_enrollment_id = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    
    error_log = models.TextField(blank=True)
    response_payload = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"{self.contact.email} -> {self.course.name} ({self.status})"

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_at"]),
        ]
