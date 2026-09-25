from django.db import migrations
from django.utils.text import slugify


def populate_slugs(apps, schema_editor):
    """Backfill slug for every existing course, deduplicating collisions
    (e.g. two courses both named 'Bootcamp') with a numeric suffix."""
    Course = apps.get_model("courses", "Course")
    seen = set(Course.objects.exclude(slug="").values_list("slug", flat=True))
    for course in Course.objects.filter(slug="").order_by("id"):
        base = slugify(course.name) or "course"
        candidate = base
        suffix = 2
        while candidate in seen:
            candidate = f"{base}-{suffix}"
            suffix += 1
        seen.add(candidate)
        course.slug = candidate
        course.save(update_fields=["slug"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("courses", "0005_course_slug"),
    ]

    operations = [
        migrations.RunPython(populate_slugs, noop_reverse),
    ]
