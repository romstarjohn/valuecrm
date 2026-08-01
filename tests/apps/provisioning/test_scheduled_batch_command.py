from unittest.mock import MagicMock

import pytest
from django.core.management import call_command

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.enrollments.models import EnrollmentAttempt
from apps.enrollments.schemas import EnrollmentResultDTO
from apps.payments.models import Payment
from apps.provisioning.models import Product, ProvisioningRequest


@pytest.fixture
def contact(db):
    return Contact.objects.create(email="student@example.com")


@pytest.fixture
def course(db):
    return Course.objects.create(cf_course_id="crs_1", name="Course 1", workspace_id="ws_1")


@pytest.fixture
def product(db, course):
    product = Product.objects.create(name="P", provisioning_policy=Product.ProvisioningPolicy.SCHEDULED)
    product.courses.add(course)
    return product


def make_request(product, contact, status):
    payment = Payment.objects.create(
        provider="tara", provider_transaction_id=f"txn_{status}_{product.id}",
        status=Payment.Status.MATCHED, match_confidence=Payment.Confidence.HIGH,
        matched_product=product, matched_contact=contact,
    )
    return ProvisioningRequest.objects.create(
        payment=payment, product=product, contact=contact,
        policy_snapshot=product.provisioning_policy, status=status,
    )


@pytest.mark.django_db
def test_batch_only_processes_scheduled_requests(product, contact, mocker):
    scheduled = make_request(product, contact, ProvisioningRequest.Status.SCHEDULED)
    pending = make_request(product, contact, ProvisioningRequest.Status.PENDING)
    failed = make_request(product, contact, ProvisioningRequest.Status.FAILED)

    enrollment_attempt = EnrollmentAttempt.objects.create(
        contact=contact, course=product.courses.first(), status=EnrollmentAttempt.Status.SUCCESS
    )
    mock_enrollment_service = MagicMock()
    mock_enrollment_service.enroll_contact.return_value = EnrollmentResultDTO(
        contact_email=contact.email, course_id="crs_1",
        status=EnrollmentAttempt.Status.SUCCESS, enrollment_attempt_id=enrollment_attempt.id,
    )
    mocker.patch(
        "apps.provisioning.services.ProvisioningService._build_enrollment_service",
        return_value=(mock_enrollment_service, MagicMock(workspace_subdomain="hammer", workspace_id="198218")),
    )

    call_command("run_scheduled_provisioning")

    scheduled.refresh_from_db()
    pending.refresh_from_db()
    failed.refresh_from_db()

    assert scheduled.status == ProvisioningRequest.Status.COMPLETED
    assert pending.status == ProvisioningRequest.Status.PENDING  # untouched
    assert failed.status == ProvisioningRequest.Status.FAILED  # untouched — not this command's job


@pytest.mark.django_db
def test_batch_handles_no_scheduled_requests_gracefully():
    call_command("run_scheduled_provisioning")  # should not raise
