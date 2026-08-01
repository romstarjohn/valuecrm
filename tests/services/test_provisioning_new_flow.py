"""
Phase 7 (docs/TARA_INTEGRATION_PROJECT.md): the new Order/course origin of
ProvisioningRequest, coexisting with the legacy Payment/Product origin
covered by tests/services/test_provisioning_service.py.
"""
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.enrollments.models import EnrollmentAttempt
from apps.enrollments.schemas import EnrollmentResultDTO
from apps.payments.models import Installment, PaymentAttempt, PaymentPlan
from apps.payments.services import OrderService, PaymentAttemptService, PaymentCreditService
from apps.provisioning.models import ProvisioningAttempt, ProvisioningRequest
from apps.provisioning.services import ProvisioningService
from integrations.clickfunnels.exceptions import ClickFunnelsAPIError, ClickFunnelsAuthError, ClickFunnelsRateLimitError

pytestmark = pytest.mark.django_db


def make_order(installment_count=1, installment_amount=Decimal("100000.00"), email="buyer@example.com", access_policy=None):
    course = Course.objects.create(cf_course_id=f"crs_{email}", name="Bootcamp", workspace_id="ws_1")
    kwargs = dict(
        code=f"plan_{email}", name="Bootcamp", course=course,
        installment_count=installment_count, installment_amount=installment_amount, is_active=True,
    )
    if access_policy is not None:
        kwargs["access_policy"] = access_policy
    plan = PaymentPlan.objects.create(**kwargs)
    contact = Contact.objects.create(email=email)
    order, _ = OrderService().create_order(contact, plan.id, f"idem-{email}")
    return order


def create_payable_attempt(installment: Installment) -> PaymentAttempt:
    attempt = PaymentAttemptService().create_attempt(installment)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    return attempt


def make_eligible_order(**kwargs):
    order = make_order(**kwargs)
    for installment in order.installments.order_by("sequence"):
        attempt = create_payable_attempt(installment)
        PaymentCreditService().apply_verified_success(attempt.id)
    order.refresh_from_db()
    return order


# --- create_request_from_order: idempotency & eligibility timing ---

def test_create_request_from_order_creates_pending_request_not_executed():
    order = make_eligible_order(email="order-flow-create@example.com")

    request = ProvisioningService().create_request_from_order(order)

    assert request.status == ProvisioningRequest.Status.PENDING
    assert request.order_id == order.id
    assert request.course_id == order.course_id
    assert request.contact_id == order.customer_id
    assert request.payment_id is None
    assert request.product_id is None


def test_create_request_from_order_is_idempotent_per_contact_course():
    order = make_eligible_order(email="order-flow-idempotent@example.com")
    service = ProvisioningService()

    first = service.create_request_from_order(order)
    second = service.create_request_from_order(order)

    assert first.id == second.id
    assert ProvisioningRequest.objects.filter(contact=order.customer, course=order.course).count() == 1


def test_full_payment_policy_creates_request_only_after_final_installment():
    order = make_order(
        installment_count=2, installment_amount=Decimal("50000.00"), email="order-flow-full-payment@example.com",
    )
    installments = list(order.installments.order_by("sequence"))

    attempt1 = create_payable_attempt(installments[0])
    PaymentCreditService().apply_verified_success(attempt1.id)
    assert ProvisioningRequest.objects.filter(order=order).count() == 0  # not yet eligible

    attempt2 = create_payable_attempt(installments[1])
    PaymentCreditService().apply_verified_success(attempt2.id)
    assert ProvisioningRequest.objects.filter(order=order).count() == 1  # now eligible — exactly one request


def test_first_installment_policy_creates_request_after_first_payment():
    order = make_order(
        installment_count=3, installment_amount=Decimal("34000.00"), email="order-flow-first-installment@example.com",
        access_policy=PaymentPlan.AccessPolicy.FIRST_INSTALLMENT,
    )
    first_installment = order.installments.order_by("sequence").first()
    attempt = create_payable_attempt(first_installment)

    PaymentCreditService().apply_verified_success(attempt.id)

    assert ProvisioningRequest.objects.filter(order=order).count() == 1


def test_duplicate_verified_success_never_creates_second_request():
    order = make_order(email="order-flow-dup-webhook@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)

    PaymentCreditService().apply_verified_success(attempt.id)
    PaymentCreditService().apply_verified_success(attempt.id)  # duplicate verified SUCCESS

    assert ProvisioningRequest.objects.filter(order=order).count() == 1


# --- execute(): course source branching for the new flow ---

def enrollment_service_bundle(status=EnrollmentAttempt.Status.SUCCESS, error_message=""):
    mock_service = MagicMock()
    mock_service.enroll_contact.return_value = EnrollmentResultDTO(
        contact_email="x@example.com", course_id="crs", status=status,
        enrollment_attempt_id=None, error_message=error_message,
    )
    return mock_service, MagicMock(workspace_subdomain="hammer", workspace_id="198218")


def test_execute_uses_order_course_cf_id_snapshot_not_live_course(mocker):
    order = make_eligible_order(email="order-flow-snapshot@example.com")
    request = ProvisioningService().create_request_from_order(order)

    # Live course cf_course_id diverges from the order's frozen snapshot —
    # the call must use the snapshot, never the live value.
    order.course.cf_course_id = "crs_changed_later"
    order.course.save(update_fields=["cf_course_id"])

    mock_service, config = enrollment_service_bundle()
    mocker.patch("apps.provisioning.services.ProvisioningService._build_enrollment_service", return_value=(mock_service, config))

    ProvisioningService().execute(request)

    _, kwargs = mock_service.enroll_contact.call_args
    assert kwargs["cf_course_id"] == order.course_cf_id
    assert kwargs["cf_course_id"] != "crs_changed_later"


def test_execute_completes_new_flow_request(mocker):
    order = make_eligible_order(email="order-flow-execute-success@example.com")
    request = ProvisioningService().create_request_from_order(order)
    mock_service, config = enrollment_service_bundle()
    mocker.patch("apps.provisioning.services.ProvisioningService._build_enrollment_service", return_value=(mock_service, config))

    result = ProvisioningService().execute(request)

    assert result.status == ProvisioningRequest.Status.COMPLETED
    assert result.attempts.filter(status=ProvisioningAttempt.Status.SUCCESS, course=order.course).count() == 1


def test_execute_new_flow_does_not_touch_legacy_payment_timeline(mocker):
    """new-flow requests have no legacy Payment — _record_success/_record_failure must not blow up."""
    order = make_eligible_order(email="order-flow-no-timeline-crash@example.com")
    request = ProvisioningService().create_request_from_order(order)
    mock_service, config = enrollment_service_bundle()
    mocker.patch("apps.provisioning.services.ProvisioningService._build_enrollment_service", return_value=(mock_service, config))

    result = ProvisioningService().execute(request)  # must not raise IntegrityError from PaymentTimelineEvent(payment=None)

    assert result.status == ProvisioningRequest.Status.COMPLETED


# --- retry classification ---

def test_missing_contact_email_is_non_retryable():
    order = make_eligible_order(email="order-flow-missing-email@example.com")
    order.customer.email = ""
    order.customer.save(update_fields=["email"])
    request = ProvisioningService().create_request_from_order(order)

    result = ProvisioningService().execute(request)

    assert result.status == ProvisioningRequest.Status.MANUAL_REVIEW
    assert result.failure_category == ProvisioningRequest.FailureCategory.MISSING_CONTACT_IDENTIFIER


def test_missing_course_cf_id_snapshot_is_non_retryable():
    order = make_eligible_order(email="order-flow-missing-course-snapshot@example.com")
    order.course_cf_id = ""
    order.save(update_fields=["course_cf_id"])
    request = ProvisioningService().create_request_from_order(order)

    result = ProvisioningService().execute(request)

    assert result.status == ProvisioningRequest.Status.MANUAL_REVIEW
    assert result.failure_category == ProvisioningRequest.FailureCategory.MISSING_COURSE_SNAPSHOT


def test_auth_error_is_non_retryable(mocker):
    order = make_eligible_order(email="order-flow-auth-error@example.com")
    request = ProvisioningService().create_request_from_order(order)
    mock_service = MagicMock()
    mock_service.enroll_contact.side_effect = ClickFunnelsAuthError("bad token", status_code=401)
    mocker.patch(
        "apps.provisioning.services.ProvisioningService._build_enrollment_service",
        return_value=(mock_service, MagicMock(workspace_subdomain="hammer", workspace_id="198218")),
    )

    result = ProvisioningService().execute(request)

    assert result.status == ProvisioningRequest.Status.MANUAL_REVIEW
    assert result.failure_category == ProvisioningRequest.FailureCategory.AUTHORIZATION_ERROR


def test_5xx_is_retryable_not_manual_review(mocker):
    order = make_eligible_order(email="order-flow-5xx@example.com")
    request = ProvisioningService().create_request_from_order(order)
    mock_service = MagicMock()
    mock_service.enroll_contact.side_effect = ClickFunnelsAPIError("server error", status_code=502)
    mocker.patch(
        "apps.provisioning.services.ProvisioningService._build_enrollment_service",
        return_value=(mock_service, MagicMock(workspace_subdomain="hammer", workspace_id="198218")),
    )
    mocker.patch("apps.provisioning.services.time.sleep")  # skip real backoff sleep

    result = ProvisioningService().execute(request)

    assert result.status == ProvisioningRequest.Status.FAILED
    assert result.failure_category == ProvisioningRequest.FailureCategory.TRANSIENT_PROVIDER_ERROR


def test_rate_limit_is_retryable(mocker):
    order = make_eligible_order(email="order-flow-rate-limit@example.com")
    request = ProvisioningService().create_request_from_order(order)
    mock_service = MagicMock()
    mock_service.enroll_contact.side_effect = ClickFunnelsRateLimitError("slow down", status_code=429)
    mocker.patch(
        "apps.provisioning.services.ProvisioningService._build_enrollment_service",
        return_value=(mock_service, MagicMock(workspace_subdomain="hammer", workspace_id="198218")),
    )
    mocker.patch("apps.provisioning.services.time.sleep")

    result = ProvisioningService().execute(request)

    assert result.status == ProvisioningRequest.Status.FAILED
    assert result.failure_category == ProvisioningRequest.FailureCategory.TRANSIENT_PROVIDER_ERROR


# --- bounded attempts ---

def test_bounded_attempts_moves_to_manual_review(mocker):
    from shared.constants import PROVISIONING_MAX_REQUEST_ATTEMPTS

    order = make_eligible_order(email="order-flow-bounded-attempts@example.com")
    request = ProvisioningService().create_request_from_order(order)
    mock_service = MagicMock()
    mock_service.enroll_contact.side_effect = ClickFunnelsAPIError("server error", status_code=503)
    mocker.patch(
        "apps.provisioning.services.ProvisioningService._build_enrollment_service",
        return_value=(mock_service, MagicMock(workspace_subdomain="hammer", workspace_id="198218")),
    )
    mocker.patch("apps.provisioning.services.time.sleep")

    service = ProvisioningService()
    # PROVISIONING_MAX_REQUEST_ATTEMPTS executes each fail retryably (FAILED);
    # the bound is enforced by _claim() at the START of the *next* attempt,
    # so one more call is needed to observe the MANUAL_REVIEW transition.
    for _ in range(PROVISIONING_MAX_REQUEST_ATTEMPTS + 1):
        request.refresh_from_db()
        service.execute(request)

    request.refresh_from_db()
    assert request.status == ProvisioningRequest.Status.MANUAL_REVIEW
    assert request.failure_category == ProvisioningRequest.FailureCategory.MAX_ATTEMPTS_EXCEEDED


# --- exactly-once: already-succeeded course is never re-enrolled ---

def test_repeat_execute_never_recalls_clickfunnels_for_already_succeeded_course(mocker):
    order = make_eligible_order(email="order-flow-no-double-enroll@example.com")
    request = ProvisioningService().create_request_from_order(order)
    ProvisioningAttempt.objects.create(
        provisioning_request=request, course=order.course, status=ProvisioningAttempt.Status.SUCCESS,
    )
    request.status = ProvisioningRequest.Status.FAILED  # e.g. a stray partial-failure retry
    request.save(update_fields=["status"])
    mock_service, config = enrollment_service_bundle()
    mocker.patch("apps.provisioning.services.ProvisioningService._build_enrollment_service", return_value=(mock_service, config))

    result = ProvisioningService().execute(request)

    mock_service.enroll_contact.assert_not_called()
    assert result.status == ProvisioningRequest.Status.COMPLETED
