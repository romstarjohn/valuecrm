import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0016_alter_paymentplan_currency'),
    ]

    operations = [
        migrations.AddField(
            model_name='tarawebhookevent',
            name='amount',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=12, null=True,
                help_text=(
                    "Amount as reported in the webhook payload, when present — uninterpreted, like raw_provider_status. "
                    "Never used to auto-credit a payment (see FailureCategory.AMOUNT_MISMATCH); stored only so an "
                    "UNCORRELATED event carries enough information for an administrator to manually attribute it "
                    "(see ReconciliationAdministrationService.attribute_webhook_event in admin_services.py)."
                ),
            ),
        ),
        migrations.AlterModelOptions(
            name='tarawebhookevent',
            options={
                'ordering': ['-received_at'],
                'permissions': [
                    ('attribute_webhook_payment', "Can manually attribute an unattributed Tara webhook payment to an order's installment"),
                ],
            },
        ),
        migrations.AlterField(
            model_name='adminauditlog',
            name='action_type',
            field=models.CharField(
                choices=[
                    ('CHECK_TARA_STATUS', 'Check Tara Status'), ('RETRY_CONFIRMATION', 'Retry Confirmation'),
                    ('RETRY_PROVISIONING', 'Retry Provisioning'), ('CANCEL_ORDER', 'Cancel Order'),
                    ('CANCEL_INSTALLMENT', 'Cancel Installment'), ('WAIVE_INSTALLMENT', 'Waive Installment'),
                    ('APPLY_MANUAL_DISPOSITION', 'Apply Manual Disposition'), ('FREEZE_ENROLLMENT', 'Freeze Enrollment'),
                    ('RESUME_ENROLLMENT', 'Resume Enrollment'), ('ATTRIBUTE_PAYMENT', 'Attribute Payment'),
                ],
                db_index=True, max_length=40,
            ),
        ),
        migrations.AlterField(
            model_name='adminauditlog',
            name='target_type',
            field=models.CharField(
                choices=[
                    ('ORDER', 'Order'), ('INSTALLMENT', 'Installment'), ('PAYMENT_ATTEMPT', 'PaymentAttempt'),
                    ('PAYMENT_CONFIRMATION', 'PaymentConfirmation'), ('PROVISIONING_REQUEST', 'ProvisioningRequest'),
                    ('ENROLLMENT_ATTEMPT', 'EnrollmentAttempt'), ('TARA_WEBHOOK_EVENT', 'TaraWebhookEvent'),
                ],
                db_index=True, max_length=30,
            ),
        ),
        migrations.AddField(
            model_name='adminauditlog',
            name='webhook_event',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='admin_audit_logs', to='payments.tarawebhookevent',
            ),
        ),
    ]
