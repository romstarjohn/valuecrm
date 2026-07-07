from django.db import models
from shared.models import TimeStampedModel

class Course(TimeStampedModel):
    cf_course_id = models.CharField(max_length=255, unique=True)
    workspace_id = models.CharField(max_length=255, db_index=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ["name"]
