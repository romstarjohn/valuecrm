"""Phase 8: ProvisioningService.retry_request — administrative hardening of the Phase 7 retry action."""
from decimal import Decimal

import pytest
from django.contrib.auth.models import User

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import AdminAuditLog, Installment, PaymentAttempt, PaymentPlan
from apps.payments.services import OrderService, PaymentAttemptService, PaymentCreditService
from apps.provisioning.models import ProvisioningRequest
from apps.provisioning.services import ProvisioningError, ProvisioningService

pytestmark = pytest.mark.django_db


def make_order(installment_count=1, installment_amount=Decimal("100000.00"), email="buyer@example.com"):
    course = Course.objects.create(cf_course_id=f"crs_{email}", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code=f"plan_{email}", name="Bootcamp", course=course,
        installment_count=installment_count, installment_amount=installment_amount, is_active=True,
    )
    contact = Contact.objects.create(email=email)
    order, _ = OrderService().create_order(contact, plan.id, f"idem-{email}")
    return order


def make_new_flow_request(email="retry@example.com", status=ProvisioningRequest.Status.FAILED):
    order = make_order(email=email)
    installment = order.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    attempt = PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentCreditService().apply_verified_success(attempt.id)
    request = ProvisioningRequest.objects.get(order__customer__email=email)
    request.status = status
    request.attempt_count = 3
    request.failure_category = ProvisioningRequest.FailureCategory.TRANSIENT_PROVIDER_ERROR
    request.save()
    return request


@pytest.fixture
def admin_user(db):
    return User.objects.create_superuser(username="retryops", email="retryops@example.com", password="x")


def test_retry_from_failed_resets_state(admin_user):
    req = make_new_flow_request(email="retry-failed@example.com", status=ProvisioningRequest.Status.FAILED)

    updated = ProvisioningService().retry_request(req.id, "operator fixed config", admin_user)

    assert updated.status == ProvisioningRequest.Status.PENDING
    assert updated.attempt_count == 0
    log = AdminAuditLog.objects.get(action_type=AdminAuditLog.ActionType.RETRY_PROVISIONING, order=req.order)
    assert log.outcome_category == AdminAuditLog.OutcomeCategory.SUCCESS
    assert "attempts=3" in log.previous_state
    assert "TRANSIENT_PROVIDER_ERROR" in log.previous_state


def test_retry_from_manual_review_resets_state(admin_user):
    req = make_new_flow_request(email="retry-manual-review@example.com", status=ProvisioningRequest.Status.MANUAL_REVIEW)

    updated = ProvisioningService().retry_request(req.id, "fixed", admin_user)

    assert updated.status == ProvisioningRequest.Status.PENDING


def test_retry_rejects_completed(admin_user):
    req = make_new_flow_request(email="retry-completed@example.com", status=ProvisioningRequest.Status.COMPLETED)

    with pytest.raises(ProvisioningError):
        ProvisioningService().retry_request(req.id, "should fail", admin_user)

    req.refresh_from_db()
    assert req.status == ProvisioningRequest.Status.COMPLETED
    log = AdminAuditLog.objects.get(action_type=AdminAuditLog.ActionType.RETRY_PROVISIONING, order=req.order)
    assert log.outcome_category == AdminAuditLog.OutcomeCategory.REJECTED_INVALID_STATE


def test_retry_rejects_pending(admin_user):
    req = make_new_flow_request(email="retry-pending@example.com", status=ProvisioningRequest.Status.PENDING)

    with pytest.raises(ProvisioningError):
        ProvisioningService().retry_request(req.id, "should fail", admin_user)


def test_retry_does_not_call_clickfunnels(admin_user, mocker):
    mock_execute = mocker.patch("apps.provisioning.services.ProvisioningService.execute")
    req = make_new_flow_request(email="retry-no-cf@example.com", status=ProvisioningRequest.Status.FAILED)

    ProvisioningService().retry_request(req.id, "reset only", admin_user)

    mock_execute.assert_not_called()


def test_retry_revalidates_order_eligibility(admin_user):
    """Defense-in-depth: if the order somehow isn't eligible anymore, reject rather than blindly reset."""
    req = make_new_flow_request(email="retry-ineligible@example.com", status=ProvisioningRequest.Status.FAILED)
    order = req.order
    installment = order.installments.get()
    installment.status = Installment.Status.SCHEDULED  # simulate no longer eligible
    installment.save()

    with pytest.raises(ProvisioningError):
        ProvisioningService().retry_request(req.id, "should be rejected", admin_user)


def test_duplicate_retry_is_safely_rejected(admin_user):
    req = make_new_flow_request(email="retry-duplicate@example.com", status=ProvisioningRequest.Status.FAILED)
    service = ProvisioningService()
    service.retry_request(req.id, "first retry", admin_user)

    with pytest.raises(ProvisioningError):
        service.retry_request(req.id, "second retry", admin_user)  # already PENDING now

    req.refresh_from_db()
    assert req.status == ProvisioningRequest.Status.PENDING  # unchanged by the rejected call
