from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0015_alter_adminauditlog_target_type'),
    ]

    operations = [
        migrations.AlterField(
            model_name='paymentplan',
            name='currency',
            field=models.CharField(choices=[('XAF', 'XAF — Central Africa CFA Franc')], default='XAF', max_length=3),
        ),
    ]
