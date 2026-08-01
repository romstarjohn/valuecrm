import pytest
from django.core.management import call_command

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import Payment, ReconciliationRun, TaraConfig
from apps.provisioning.models import Product, ProductMapping
from shared.security import encrypt_value


@pytest.fixture
def tara_config(db):
    return TaraConfig.objects.create(
        name="Main", is_active=True,
        api_key=encrypt_value("key"), webhook_secret=encrypt_value("secret"),
        validation_status=TaraConfig.ValidationStatus.VALID,
    )


@pytest.fixture
def product(db):
    course = Course.objects.create(cf_course_id="crs_1", name="Course 1", workspace_id="ws_1")
    product = Product.objects.create(name="Bootcamp", provisioning_policy=Product.ProvisioningPolicy.MANUAL)
    product.courses.add(course)
    ProductMapping.objects.create(provider="tara", external_ref="ref_1", product=product)
    return product


@pytest.mark.django_db
def test_reconcile_runs_cleanly_with_no_stale_payments():
    call_command("reconcile_tara_payments")
    run = ReconciliationRun.objects.get()
    assert run.completed_at is not None
    assert run.errors == 0


@pytest.mark.django_db
def test_reconcile_makes_no_tara_call(mocker):
    """Phase 9: this command no longer pulls the transaction list — it only rechecks local NEEDS_REVIEW payments."""
    mock_list = mocker.patch("integrations.payments.tara.client.TaraClient.list_paid_transactions")
    mock_status = mocker.patch("integrations.payments.tara.client.TaraClient.check_transaction_status")

    call_command("reconcile_tara_payments")

    mock_list.assert_not_called()
    mock_status.assert_not_called()


@pytest.mark.django_db
def test_reconcile_rechecks_stale_needs_review_within_window(tara_config, product):
    payment = Payment.objects.create(
        provider="tara", provider_transaction_id="txn_stale", product_ref="ref_1",
        extracted_phone="+15550001111", extracted_email="student@example.com",
        status=Payment.Status.NEEDS_REVIEW,
    )
    # Operator fixes the missing contact after the fact.
    Contact.objects.create(email="student@example.com", phone="+15550001111")

    call_command("reconcile_tara_payments")

    payment.refresh_from_db()
    assert payment.status == Payment.Status.MATCHED
    run = ReconciliationRun.objects.get()
    assert run.errors == 0
