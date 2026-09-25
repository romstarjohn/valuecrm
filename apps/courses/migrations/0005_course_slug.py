from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("courses", "0004_monafrolibre_default_offer"),
    ]

    operations = [
        migrations.AddField(
            model_name="course",
            name="slug",
            field=models.SlugField(blank=True, default="", max_length=255),
            preserve_default=False,
        ),
    ]
