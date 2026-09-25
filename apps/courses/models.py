from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.templatetags.static import static
from django.utils.text import slugify
from shared.models import TimeStampedModel

class Course(TimeStampedModel):
    cf_course_id = models.CharField(max_length=255, unique=True)
    workspace_id = models.CharField(max_length=255, db_index=True)
    name = models.CharField(max_length=255)
    slug = models.SlugField(
        max_length=255, unique=True, blank=True,
        help_text="Identifies this course/product in its public checkout URL (/paiement/<slug>/). "
                   "Auto-generated from the name if left blank.",
    )
    description = models.TextField(blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name) or "course"
            candidate = base
            suffix = 2
            while Course.objects.exclude(pk=self.pk).filter(slug=candidate).exists():
                candidate = f"{base}-{suffix}"
                suffix += 1
            self.slug = candidate
        super().save(*args, **kwargs)

    class Meta:
        ordering = ["name"]


def validate_mockup_image(value):
    """Allow public HTTPS artwork or a path within our static assets."""
    if not value:
        return
    if value.startswith("https://"):
        URLValidator(schemes=["https"])(value)
    elif not value.startswith("images/") or ".." in value or "\\" in value:
        raise ValidationError("Use an HTTPS image URL or a static path starting with images/.")


class CheckoutOffer(TimeStampedModel):
    course = models.OneToOneField(
        Course, null=True, blank=True, on_delete=models.SET_NULL, related_name="checkout_offer",
        help_text="Attach an override to a course, or leave blank for the shared default template.",
    )
    is_default = models.BooleanField(default=False, help_text="Used by every course without its own offer profile.")
    slug = models.SlugField(unique=True)
    title = models.CharField(max_length=255)
    subtitle = models.TextField(blank=True)
    language = models.CharField(max_length=2, choices=[("en", "English"), ("fr", "French")], default="en")
    mockup_image = models.CharField(max_length=1000, validators=[validate_mockup_image])
    mockup_alt = models.CharField(max_length=255)
    highlights = models.TextField(blank=True, help_text="Short facts, one per line (e.g. No prior knowledge required).")
    closing_note = models.TextField(blank=True)

    @property
    def image_url(self):
        return self.mockup_image if self.mockup_image.startswith("https://") else static(self.mockup_image)

    @property
    def highlight_list(self):
        return [line.strip() for line in self.highlights.splitlines() if line.strip()]

    def __str__(self):
        return self.title

    class Meta:
        ordering = ["title"]
        constraints = [
            models.UniqueConstraint(fields=["is_default"], condition=models.Q(is_default=True), name="one_default_checkout_offer"),
        ]


class CheckoutBenefit(models.Model):
    offer = models.ForeignKey(CheckoutOffer, on_delete=models.CASCADE, related_name="benefits")
    title = models.CharField(max_length=255)
    description = models.TextField()
    is_bonus = models.BooleanField(default=False)
    display_order = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.title

    class Meta:
        ordering = ["display_order", "id"]
