from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("courses", "0006_populate_course_slugs"),
    ]

    operations = [
        migrations.AlterField(
            model_name="course",
            name="slug",
            field=models.SlugField(
                blank=True, max_length=255, unique=True,
                help_text="Identifies this course/product in its public checkout URL "
                           "(/paiement/<slug>/). Auto-generated from the name if left blank.",
            ),
        ),
    ]
