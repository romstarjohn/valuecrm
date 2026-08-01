from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.enrollments.models import EnrollmentAttempt
from apps.enrollments.schemas import EnrollmentResultDTO
from apps.payments.models import Payment, PaymentTimelineEvent
from apps.provisioning.models import Product, ProvisioningAttempt, ProvisioningRequest
from apps.provisioning.services import ProvisioningService
from integrations.clickfunnels.exceptions import ClickFunnelsAPIError, ClickFunnelsAuthError


@pytest.fixture
def contact(db):
    return Contact.objects.create(email="student@example.com", cf_contact_id="con_1")


@pytest.fixture
def course(db):
    return Course.objects.create(cf_course_id="crs_1", name="Course 1", workspace_id="ws_1")


def make_matched_payment(product, contact):
    payment = Payment.objects.create(
        provider="tara",
        provider_transaction_id=f"txn_{product.id}_{contact.id}",
        status=Payment.Status.MATCHED,
        match_confidence=Payment.Confidence.HIGH,
        matched_product=product,
        matched_contact=contact,
    )
    return payment


@pytest.mark.django_db
def test_create_request_refuses_low_confidence_payment(course):
    product = Product.objects.create(name="P", provisioning_policy=Product.ProvisioningPolicy.AUTOMATIC)
    product.courses.add(course)
    payment = Payment.objects.create(
        provider="tara", provider_transaction_id="txn_low",
        status=Payment.Status.NEEDS_REVIEW, match_confidence=Payment.Confidence.MEDIUM,
        matched_product=product,
    )

    request = ProvisioningService().create_request_from_payment(payment)

    assert request is None
    assert ProvisioningRequest.objects.count() == 0


@pytest.mark.django_db
def test_automatic_policy_executes_inline(course, contact, mocker):
    product = Product.objects.create(name="P", provisioning_policy=Product.ProvisioningPolicy.AUTOMATIC)
    product.courses.add(course)
    payment = make_matched_payment(product, contact)
    enrollment_attempt = EnrollmentAttempt.objects.create(
        contact=contact, course=course, status=EnrollmentAttempt.Status.SUCCESS
    )

    mocker.patch(
        "apps.provisioning.services.ProvisioningService._build_enrollment_service",
        return_value=(MagicMock(enroll_contact=MagicMock(return_value=EnrollmentResultDTO(
            contact_email=contact.email, course_id=course.cf_course_id,
            status=EnrollmentAttempt.Status.SUCCESS, enrollment_attempt_id=enrollment_attempt.id,
        ))), MagicMock(workspace_subdomain="hammer", workspace_id="198218")),
    )

    request = ProvisioningService().create_request_from_payment(payment)

    assert request.status == ProvisioningRequest.Status.COMPLETED
    assert request.attempts.count() == 1
    assert request.attempts.first().status == ProvisioningAttempt.Status.SUCCESS


@pytest.mark.django_db
@pytest.mark.parametrize("policy,expected_status", [
    (Product.ProvisioningPolicy.MANUAL, ProvisioningRequest.Status.PENDING),
    (Product.ProvisioningPolicy.SCHEDULED, ProvisioningRequest.Status.SCHEDULED),
    (Product.ProvisioningPolicy.APPROVAL_REQUIRED, ProvisioningRequest.Status.AWAITING_APPROVAL),
])
def test_non_automatic_policies_stop_and_wait(course, contact, policy, expected_status, mocker):
    product = Product.objects.create(name="P", provisioning_policy=policy)
    product.courses.add(course)
    payment = make_matched_payment(product, contact)

    execute_mock = mocker.patch("apps.provisioning.services.ProvisioningService.execute")
    request = ProvisioningService().create_request_from_payment(payment)

    assert request.status == expected_status
    execute_mock.assert_not_called()


@pytest.mark.django_db
def test_create_request_is_idempotent_per_payment(course, contact):
    product = Product.objects.create(name="P", provisioning_policy=Product.ProvisioningPolicy.MANUAL)
    product.courses.add(course)
    payment = make_matched_payment(product, contact)

    service = ProvisioningService()
    first = service.create_request_from_payment(payment)
    second = service.create_request_from_payment(payment)

    assert first.id == second.id
    assert ProvisioningRequest.objects.filter(payment=payment).count() == 1


@pytest.mark.django_db
def test_execute_partial_failure_marks_failed_and_records_timeline(contact, mocker):
    course1 = Course.objects.create(cf_course_id="c1", name="C1", workspace_id="ws_1")
    course2 = Course.objects.create(cf_course_id="c2", name="C2", workspace_id="ws_1")
    product = Product.objects.create(name="P", provisioning_policy=Product.ProvisioningPolicy.MANUAL)
    product.courses.add(course1, course2)
    payment = make_matched_payment(product, contact)

    request = ProvisioningRequest.objects.create(
        payment=payment, product=product, contact=contact, policy_snapshot=product.provisioning_policy,
        status=ProvisioningRequest.Status.PENDING,
    )

    success_attempt = EnrollmentAttempt.objects.create(contact=contact, course=course1, status=EnrollmentAttempt.Status.SUCCESS)
    failure_attempt = EnrollmentAttempt.objects.create(contact=contact, course=course2, status=EnrollmentAttempt.Status.FAILURE)
    mock_enrollment_service = MagicMock()
    mock_enrollment_service.enroll_contact.side_effect = [
        EnrollmentResultDTO(contact_email=contact.email, course_id="c1", status=EnrollmentAttempt.Status.SUCCESS, enrollment_attempt_id=success_attempt.id),
        EnrollmentResultDTO(contact_email=contact.email, course_id="c2", status=EnrollmentAttempt.Status.FAILURE, enrollment_attempt_id=failure_attempt.id, error_message="boom"),
    ]
    mocker.patch(
        "apps.provisioning.services.ProvisioningService._build_enrollment_service",
        return_value=(mock_enrollment_service, MagicMock(workspace_subdomain="hammer", workspace_id="198218")),
    )

    result = ProvisioningService().execute(request)

    assert result.status == ProvisioningRequest.Status.FAILED
    assert result.attempts.count() == 2
    assert result.attempts.filter(status=ProvisioningAttempt.Status.SUCCESS).count() == 1
    assert result.attempts.filter(status=ProvisioningAttempt.Status.FAILURE).count() == 1
    assert payment.timeline_events.filter(event_type=PaymentTimelineEvent.EventType.PROVISIONING_FAILED).exists()


@pytest.mark.django_db
def test_retry_skips_already_succeeded_courses(contact, mocker):
    course1 = Course.objects.create(cf_course_id="c1", name="C1", workspace_id="ws_1")
    course2 = Course.objects.create(cf_course_id="c2", name="C2", workspace_id="ws_1")
    product = Product.objects.create(name="P", provisioning_policy=Product.ProvisioningPolicy.MANUAL)
    product.courses.add(course1, course2)
    payment = make_matched_payment(product, contact)
    request = ProvisioningRequest.objects.create(
        payment=payment, product=product, contact=contact, policy_snapshot=product.provisioning_policy,
        status=ProvisioningRequest.Status.FAILED,
    )
    # course1 already succeeded in a prior attempt
    ProvisioningAttempt.objects.create(provisioning_request=request, course=course1, status=ProvisioningAttempt.Status.SUCCESS)

    course2_attempt = EnrollmentAttempt.objects.create(contact=contact, course=course2, status=EnrollmentAttempt.Status.SUCCESS)
    mock_enrollment_service = MagicMock()
    mock_enrollment_service.enroll_contact.return_value = EnrollmentResultDTO(
        contact_email=contact.email, course_id="c2", status=EnrollmentAttempt.Status.SUCCESS, enrollment_attempt_id=course2_attempt.id,
    )
    mocker.patch(
        "apps.provisioning.services.ProvisioningService._build_enrollment_service",
        return_value=(mock_enrollment_service, MagicMock(workspace_subdomain="hammer", workspace_id="198218")),
    )

    result = ProvisioningService().execute(request)

    assert result.status == ProvisioningRequest.Status.COMPLETED
    # only course2 was (re-)attempted this time
    mock_enrollment_service.enroll_contact.assert_called_once()
    assert result.attempts.filter(course=course2).count() == 1


@pytest.mark.django_db
def test_execute_without_active_config_fails_cleanly(contact, mocker):
    course = Course.objects.create(cf_course_id="c1", name="C1", workspace_id="ws_1")
    product = Product.objects.create(name="P", provisioning_policy=Product.ProvisioningPolicy.MANUAL)
    product.courses.add(course)
    payment = make_matched_payment(product, contact)
    request = ProvisioningRequest.objects.create(
        payment=payment, product=product, contact=contact, policy_snapshot=product.provisioning_policy,
        status=ProvisioningRequest.Status.PENDING,
    )
    mocker.patch("apps.provisioning.services.ProvisioningService._build_enrollment_service", return_value=None)

    result = ProvisioningService().execute(request)

    # Phase 7: "no active ClickFunnels configuration" is a non-retryable
    # (CONFIGURATION_ERROR) category — it goes straight to MANUAL_REVIEW
    # rather than the retryable FAILED state, since retrying without an
    # operator fixing the configuration can never succeed.
    assert result.status == ProvisioningRequest.Status.MANUAL_REVIEW
    assert result.failure_category == ProvisioningRequest.FailureCategory.CONFIGURATION_ERROR
    assert "No active ClickFunnels" in result.last_error


@pytest.mark.django_db
def test_approve_transitions_awaiting_approval_to_pending(contact, course):
    product = Product.objects.create(name="P", provisioning_policy=Product.ProvisioningPolicy.APPROVAL_REQUIRED)
    product.courses.add(course)
    payment = make_matched_payment(product, contact)
    request = ProvisioningRequest.objects.create(
        payment=payment, product=product, contact=contact, policy_snapshot=product.provisioning_policy,
        status=ProvisioningRequest.Status.AWAITING_APPROVAL,
    )

    from django.contrib.auth.models import User
    user = User.objects.create_user(username="ops", password="x")
    updated = ProvisioningService().approve(request, user)

    assert updated.status == ProvisioningRequest.Status.PENDING
    assert updated.approved_by == user
    assert updated.approved_at is not None


@pytest.mark.django_db
def test_cancel_sets_cancelled_status(contact, course):
    product = Product.objects.create(name="P", provisioning_policy=Product.ProvisioningPolicy.MANUAL)
    product.courses.add(course)
    payment = make_matched_payment(product, contact)
    request = ProvisioningRequest.objects.create(
        payment=payment, product=product, contact=contact, policy_snapshot=product.provisioning_policy,
        status=ProvisioningRequest.Status.FAILED,
    )

    updated = ProvisioningService().cancel(request)

    assert updated.status == ProvisioningRequest.Status.CANCELLED
