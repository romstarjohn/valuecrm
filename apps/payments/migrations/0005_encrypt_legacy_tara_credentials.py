# Data migration — no credentials are placed in this file. See
# ./_encrypt_legacy_credentials.py for the (independently unit-tested) logic.
# Safe to run against a database with zero, some, or already-encrypted rows.

from django.db import migrations

from ._encrypt_legacy_credentials import encrypt_legacy_plaintext_credentials


def encrypt_legacy_credentials(apps, schema_editor):
    TaraConfig = apps.get_model("payments", "TaraConfig")
    encrypt_legacy_plaintext_credentials(TaraConfig)


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0004_taraconfig_business_id_alter_taraconfig_is_active_and_more"),
    ]

    operations = [
        migrations.RunPython(encrypt_legacy_credentials, migrations.RunPython.noop),
    ]
