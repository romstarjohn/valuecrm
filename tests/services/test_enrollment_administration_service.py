"""
Phase 9: service-level coverage for EnrollmentAdministrationService
(apps/payments/admin_services.py) — freeze/resume of a customer's
ClickFunnels course enrollment.

Two entry points are covered:
- freeze_enrollment_attempt/resume_enrollment_attempt: the universal core,
  works on any successful EnrollmentAttempt, order-linked or not (manual/
  bulk enrollments via apps.enrollments have no Order at all).
- freeze_order_enrollment/resume_order_enrollment: the Order-centric wrapper
  used by the operations hub, which resolves down to an EnrollmentAttempt
  and delegates, additionally mirroring Order.status.
"""
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from django.contrib.auth.models import User

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.enrollments.models import EnrollmentAttempt
from apps.payments.admin_services import AdminActionError, EnrollmentAdministrationService
from apps.payments.models import AdminAuditLog, Order, PaymentAttempt, PaymentPlan
from apps.payments.services import OrderService, PaymentAttemptService, PaymentCreditService
from apps.provisioning.models import ProvisioningAttempt, ProvisioningRequest

pytestmark = pytest.mark.django_db


def make_eligible_order(email="buyer@example.com"):
    """An ACTIVE (not COMPLETED) order with a real ProvisioningRequest, created
    the same way the checkout flow does. Two installments, only the first
    paid — access-eligible (FIRST_INSTALLMENT default policy) but not fully
    settled, so the order lands on ACTIVE rather than COMPLETED, matching a
    real in-progress installment plan (freeze/resume targets ACTIVE/PAST_DUE
    orders, never COMPLETED ones)."""
    course = Course.objects.create(cf_course_id=f"crs_{email}", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code=f"plan_{email}", name="Bootcamp", course=course,
        installment_count=2, installment_amount=Decimal("50000.00"), is_active=True,
        access_policy=PaymentPlan.AccessPolicy.FIRST_INSTALLMENT,
    )
    contact = Contact.objects.create(email=email)
    order, _ = OrderService().create_order(contact, plan.id, f"idem-{email}")
    first_installment = order.installments.order_by("sequence").first()
    attempt = PaymentAttemptService().create_attempt(first_installment)
    attempt = PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentCreditService().apply_verified_success(attempt.id)
    order.refresh_from_db()
    return order


def make_successful_enrollment(order, cf_enrollment_id="cf_enr_1"):
    """Attach a SUCCESS ProvisioningAttempt + EnrollmentAttempt to the order's
    ProvisioningRequest, as if EnrollmentService.enroll_contact had already
    succeeded — without going through the real ClickFunnels call."""
    provisioning_request = ProvisioningRequest.objects.get(contact_id=order.customer_id, course_id=order.course_id)
    enrollment_attempt = EnrollmentAttempt.objects.create(
        contact=order.customer, course=order.course,
        status=EnrollmentAttempt.Status.SUCCESS, cf_enrollment_id=cf_enrollment_id,
    )
    ProvisioningAttempt.objects.create(
        provisioning_request=provisioning_request, course=order.course,
        enrollment_attempt=enrollment_attempt, status=ProvisioningAttempt.Status.SUCCESS,
    )
    return enrollment_attempt


def make_standalone_enrollment(email="standalone@example.com", cf_enrollment_id="cf_enr_standalone"):
    """A SUCCESS EnrollmentAttempt with no Order/ProvisioningRequest behind
    it at all — the shape of every enrollment created via apps.enrollments'
    manual "New Enrollment"/bulk-enroll forms, which predate and remain
    decoupled from the checkout/payments pipeline."""
    contact = Contact.objects.create(email=email)
    course = Course.objects.create(cf_course_id=f"crs_{email}", name="Bootcamp", workspace_id="ws_1")
    return EnrollmentAttempt.objects.create(
        contact=contact, course=course,
        status=EnrollmentAttempt.Status.SUCCESS, cf_enrollment_id=cf_enrollment_id,
    )


@pytest.fixture
def admin_user(db):
    return User.objects.create_superuser(username="ops", email="ops@example.com", password="x")


def mock_bundle(mocker, suspend_side_effect=None):
    mock_enrollment_service = MagicMock()
    if suspend_side_effect is not None:
        mock_enrollment_service.set_enrollment_suspension.side_effect = suspend_side_effect
    mock_config = MagicMock(workspace_subdomain="hammer")
    mocker.patch(
        "apps.payments.admin_services.EnrollmentAdministrationService._build_enrollment_service",
        return_value=(mock_enrollment_service, mock_config),
    )
    return mock_enrollment_service


# --- freeze_enrollment_attempt / resume_enrollment_attempt (universal core) ---

def test_freeze_enrollment_attempt_success_no_order(admin_user, mocker):
    """The whole point: works with no Order/ProvisioningRequest at all."""
    enrollment_attempt = make_standalone_enrollment(cf_enrollment_id="cf_enr_standalone_freeze")
    mock_service = mock_bundle(mocker)

    updated = EnrollmentAdministrationService().freeze_enrollment_attempt(
        enrollment_attempt.id, "customer stopped paying", admin_user,
    )

    assert updated.cf_suspended is True
    assert updated.cf_suspended_at is not None
    assert updated.cf_suspension_reason == "customer stopped paying"
    mock_service.set_enrollment_suspension.assert_called_once_with(
        workspace_subdomain="hammer", cf_enrollment_id="cf_enr_standalone_freeze",
        suspended=True, reason="customer stopped paying",
    )
    log = AdminAuditLog.objects.get(
        target_type=AdminAuditLog.TargetType.ENROLLMENT_ATTEMPT, action_type=AdminAuditLog.ActionType.FREEZE_ENROLLMENT,
    )
    assert log.outcome_category == AdminAuditLog.OutcomeCategory.SUCCESS
    assert log.order is None


def test_resume_enrollment_attempt_success_no_order(admin_user, mocker):
    enrollment_attempt = make_standalone_enrollment(cf_enrollment_id="cf_enr_standalone_resume")
    mock_service = mock_bundle(mocker)
    EnrollmentAdministrationService().freeze_enrollment_attempt(enrollment_attempt.id, "frozen", admin_user)

    updated = EnrollmentAdministrationService().resume_enrollment_attempt(
        enrollment_attempt.id, "payment resumed", admin_user,
    )

    assert updated.cf_suspended is False
    assert updated.cf_suspended_at is None
    assert updated.cf_suspension_reason == ""
    mock_service.set_enrollment_suspension.assert_called_with(
        workspace_subdomain="hammer", cf_enrollment_id="cf_enr_standalone_resume",
        suspended=False, reason="payment resumed",
    )
    log = AdminAuditLog.objects.get(
        target_type=AdminAuditLog.TargetType.ENROLLMENT_ATTEMPT, action_type=AdminAuditLog.ActionType.RESUME_ENROLLMENT,
    )
    assert log.outcome_category == AdminAuditLog.OutcomeCategory.SUCCESS


def test_freeze_enrollment_attempt_idempotent(admin_user, mocker):
    enrollment_attempt = make_standalone_enrollment()
    mock_service = mock_bundle(mocker)
    EnrollmentAdministrationService().freeze_enrollment_attempt(enrollment_attempt.id, "first freeze", admin_user)
    mock_service.set_enrollment_suspension.reset_mock()

    EnrollmentAdministrationService().freeze_enrollment_attempt(enrollment_attempt.id, "second attempt", admin_user)

    mock_service.set_enrollment_suspension.assert_not_called()
    assert AdminAuditLog.objects.filter(
        action_type=AdminAuditLog.ActionType.FREEZE_ENROLLMENT,
        outcome_category=AdminAuditLog.OutcomeCategory.NO_OP_ALREADY_IN_STATE,
    ).exists()


def test_resume_enrollment_attempt_idempotent_when_not_suspended(admin_user, mocker):
    enrollment_attempt = make_standalone_enrollment()
    mock_service = mock_bundle(mocker)

    EnrollmentAdministrationService().resume_enrollment_attempt(enrollment_attempt.id, "not frozen", admin_user)

    mock_service.set_enrollment_suspension.assert_not_called()
    log = AdminAuditLog.objects.get(action_type=AdminAuditLog.ActionType.RESUME_ENROLLMENT)
    assert log.outcome_category == AdminAuditLog.OutcomeCategory.NO_OP_ALREADY_IN_STATE


def test_freeze_enrollment_attempt_requires_success_status_and_cf_id(admin_user, mocker):
    contact = Contact.objects.create(email="failed-attempt@example.com")
    course = Course.objects.create(cf_course_id="crs_failed", name="Bootcamp", workspace_id="ws_1")
    failed_attempt = EnrollmentAttempt.objects.create(
        contact=contact, course=course, status=EnrollmentAttempt.Status.FAILURE,
    )
    mock_service = mock_bundle(mocker)

    with pytest.raises(AdminActionError, match="Aucun accès ouvert dans ClickFunnels pour cette inscription"):
        EnrollmentAdministrationService().freeze_enrollment_attempt(failed_attempt.id, "reason", admin_user)

    mock_service.set_enrollment_suspension.assert_not_called()


def test_freeze_enrollment_attempt_clickfunnels_error_leaves_local_state_untouched(admin_user, mocker):
    enrollment_attempt = make_standalone_enrollment()
    mock_bundle(mocker, suspend_side_effect=Exception("ClickFunnels API Error"))

    with pytest.raises(AdminActionError, match="ClickFunnels n'a pas pu être mis à jour"):
        EnrollmentAdministrationService().freeze_enrollment_attempt(enrollment_attempt.id, "reason", admin_user)

    enrollment_attempt.refresh_from_db()
    assert enrollment_attempt.cf_suspended is False
    log = AdminAuditLog.objects.get(action_type=AdminAuditLog.ActionType.FREEZE_ENROLLMENT)
    assert log.outcome_category == AdminAuditLog.OutcomeCategory.FAILED


# --- freeze_order_enrollment / resume_order_enrollment (Order-centric wrapper) ---

def test_freeze_order_enrollment_success(admin_user, mocker):
    order = make_eligible_order(email="freeze-order-success@example.com")
    enrollment_attempt = make_successful_enrollment(order, cf_enrollment_id="cf_enr_freeze_order")
    mock_service = mock_bundle(mocker)

    updated = EnrollmentAdministrationService().freeze_order_enrollment(order.id, "customer stopped paying", admin_user)

    assert updated.status == Order.Status.SUSPENDED
    enrollment_attempt.refresh_from_db()
    assert enrollment_attempt.cf_suspended is True
    mock_service.set_enrollment_suspension.assert_called_once_with(
        workspace_subdomain="hammer", cf_enrollment_id="cf_enr_freeze_order",
        suspended=True, reason="customer stopped paying",
    )
    order_log = AdminAuditLog.objects.get(order=order, action_type=AdminAuditLog.ActionType.FREEZE_ENROLLMENT)
    assert order_log.outcome_category == AdminAuditLog.OutcomeCategory.SUCCESS
    assert order_log.target_type == AdminAuditLog.TargetType.ORDER
    attempt_log = AdminAuditLog.objects.get(
        target_type=AdminAuditLog.TargetType.ENROLLMENT_ATTEMPT, action_type=AdminAuditLog.ActionType.FREEZE_ENROLLMENT,
    )
    assert attempt_log.outcome_category == AdminAuditLog.OutcomeCategory.SUCCESS


def test_resume_order_enrollment_success(admin_user, mocker):
    order = make_eligible_order(email="resume-order-success@example.com")
    make_successful_enrollment(order, cf_enrollment_id="cf_enr_resume_order")
    mock_service = mock_bundle(mocker)
    EnrollmentAdministrationService().freeze_order_enrollment(order.id, "frozen", admin_user)

    updated = EnrollmentAdministrationService().resume_order_enrollment(order.id, "payment resumed", admin_user)

    assert updated.status == Order.Status.ACTIVE
    mock_service.set_enrollment_suspension.assert_called_with(
        workspace_subdomain="hammer", cf_enrollment_id="cf_enr_resume_order",
        suspended=False, reason="payment resumed",
    )
    log = AdminAuditLog.objects.get(order=order, action_type=AdminAuditLog.ActionType.RESUME_ENROLLMENT)
    assert log.outcome_category == AdminAuditLog.OutcomeCategory.SUCCESS


def test_freeze_order_enrollment_no_provisioning_request(admin_user, mocker):
    order = make_eligible_order(email="freeze-order-no-pr@example.com")
    ProvisioningRequest.objects.filter(contact_id=order.customer_id, course_id=order.course_id).delete()
    mock_service = mock_bundle(mocker)

    with pytest.raises(AdminActionError, match="rien à suspendre"):
        EnrollmentAdministrationService().freeze_order_enrollment(order.id, "reason", admin_user)

    mock_service.set_enrollment_suspension.assert_not_called()
    log = AdminAuditLog.objects.get(order=order, action_type=AdminAuditLog.ActionType.FREEZE_ENROLLMENT)
    assert log.outcome_category == AdminAuditLog.OutcomeCategory.REJECTED_INVALID_STATE
    assert log.target_type == AdminAuditLog.TargetType.ORDER


def test_freeze_order_enrollment_no_successful_enrollment_attempt(admin_user, mocker):
    order = make_eligible_order(email="freeze-order-no-attempt@example.com")
    # ProvisioningRequest exists (created by apply_verified_success) but no
    # SUCCESS ProvisioningAttempt/EnrollmentAttempt was ever created.
    mock_service = mock_bundle(mocker)

    with pytest.raises(AdminActionError, match="Aucun accès ouvert dans ClickFunnels pour cette vente"):
        EnrollmentAdministrationService().freeze_order_enrollment(order.id, "reason", admin_user)

    mock_service.set_enrollment_suspension.assert_not_called()
    log = AdminAuditLog.objects.get(order=order, action_type=AdminAuditLog.ActionType.FREEZE_ENROLLMENT)
    assert log.outcome_category == AdminAuditLog.OutcomeCategory.REJECTED_INVALID_STATE
    assert log.target_type == AdminAuditLog.TargetType.ORDER


def test_freeze_order_enrollment_invalid_order_status(admin_user, mocker):
    order = make_eligible_order(email="freeze-order-invalid-status@example.com")
    make_successful_enrollment(order)
    mock_service = mock_bundle(mocker)
    order.status = Order.Status.COMPLETED
    order.save(update_fields=["status"])

    with pytest.raises(AdminActionError, match="pas possible dans l.état actuel"):
        EnrollmentAdministrationService().freeze_order_enrollment(order.id, "reason", admin_user)

    mock_service.set_enrollment_suspension.assert_not_called()
