from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import Installment, Order, PaymentAttempt, PaymentConfirmation, PaymentPlan
from apps.payments.services import (
    DuplicatePaymentError,
    OrderService,
    PaymentAttemptService,
    PaymentCreditService,
    PaymentIdConflictError,
)

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
    """
    A real attempt reaches SUCCEEDED via CREATED -> LINK_CREATED -> PENDING ->
    SUCCEEDED (Phase 5 creates it, gets a link, then a webhook/status-check
    confirms it) — never straight from CREATED. This helper simulates that
    realistic precondition so these tests exercise PaymentCreditService
    against a state the real system would actually produce.
    """
    attempt = PaymentAttemptService().create_attempt(installment)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    return attempt


@pytest.fixture
def service():
    return PaymentCreditService()


# --- apply_verified_success: single-installment order ---

def test_apply_verified_success_transitions_attempt_and_installment(service):
    order = make_order()
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)

    updated = service.apply_verified_success(attempt.id)

    installment.refresh_from_db()
    assert updated.status == PaymentAttempt.Status.SUCCEEDED
    assert installment.status == Installment.Status.PAID
    assert installment.paid_amount == installment.expected_amount
    assert installment.paid_at is not None


def test_single_installment_order_becomes_completed(service):
    order = make_order()
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)

    service.apply_verified_success(attempt.id)

    order.refresh_from_db()
    assert order.status == Order.Status.COMPLETED


def test_partial_payment_on_multi_installment_order_becomes_active(service):
    order = make_order(installment_count=3, installment_amount=Decimal("34000.00"), email="multi@example.com")
    first_installment = order.installments.order_by("sequence").first()
    attempt = create_payable_attempt(first_installment)

    service.apply_verified_success(attempt.id)

    order.refresh_from_db()
    assert order.status == Order.Status.ACTIVE
    assert order.installments.filter(status=Installment.Status.PAID).count() == 1
    assert order.installments.exclude(status=Installment.Status.PAID).count() == 2


def test_all_installments_paid_makes_order_completed(service):
    order = make_order(installment_count=2, installment_amount=Decimal("50000.00"), email="allpaid@example.com")
    installments = list(order.installments.order_by("sequence"))

    for installment in installments:
        attempt = create_payable_attempt(installment)
        service.apply_verified_success(attempt.id)

    order.refresh_from_db()
    assert order.status == Order.Status.COMPLETED


def test_tara_payment_id_is_set_when_provided(service):
    order = make_order()
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)

    service.apply_verified_success(attempt.id, tara_payment_id="tara-pay-123")

    attempt.refresh_from_db()
    assert attempt.tara_payment_id == "tara-pay-123"


# --- Idempotency ---

def test_duplicate_verified_success_credits_once(service):
    order = make_order()
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)

    service.apply_verified_success(attempt.id)
    result = service.apply_verified_success(attempt.id)  # duplicate call — safe no-op

    installment.refresh_from_db()
    assert result.status == PaymentAttempt.Status.SUCCEEDED
    assert installment.status == Installment.Status.PAID
    assert installment.paid_amount == installment.expected_amount  # unchanged, not doubled


# --- Cancelled/suspended orders never rewritten ---

def test_cancelled_order_status_not_rewritten_but_payment_history_preserved(service):
    order = make_order()
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)
    OrderService().cancel_order(order)

    service.apply_verified_success(attempt.id)

    order.refresh_from_db()
    installment.refresh_from_db()
    assert order.status == Order.Status.CANCELLED  # never rewritten
    assert installment.status == Installment.Status.PAID  # payment history remains true regardless
    assert installment.paid_amount == installment.expected_amount


# --- Access eligibility ---

def test_access_eligibility_correct_for_full_payment_policy(service):
    order = make_order(installment_count=2, installment_amount=Decimal("50000.00"), email="eligibility@example.com")
    installments = list(order.installments.order_by("sequence"))
    assert order.access_policy == PaymentPlan.AccessPolicy.FULL_PAYMENT

    attempt = create_payable_attempt(installments[0])
    service.apply_verified_success(attempt.id)
    order.refresh_from_db()
    assert order.is_access_eligible is False  # only 1 of 2 paid

    attempt2 = create_payable_attempt(installments[1])
    service.apply_verified_success(attempt2.id)
    order.refresh_from_db()
    assert order.is_access_eligible is True  # all paid


def test_access_eligibility_correct_for_first_installment_policy():
    course = Course.objects.create(cf_course_id="crs_first_inst", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code="plan-first-inst", name="Bootcamp", course=course, installment_count=3,
        installment_amount=Decimal("34000.00"), access_policy=PaymentPlan.AccessPolicy.FIRST_INSTALLMENT, is_active=True,
    )
    contact = Contact.objects.create(email="first-inst@example.com")
    order, _ = OrderService().create_order(contact, plan.id, "idem-first-inst")
    first_installment = order.installments.order_by("sequence").first()
    attempt = create_payable_attempt(first_installment)

    assert order.is_access_eligible is False
    PaymentCreditService().apply_verified_success(attempt.id)
    order.refresh_from_db()
    assert order.is_access_eligible is True  # first installment alone is enough under this policy


# --- Amount/currency invariants ---

def test_paid_amount_matches_expected_installment_amount(service):
    order = make_order(installment_amount=Decimal("77777.00"), email="amount-check@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)

    service.apply_verified_success(attempt.id)

    installment.refresh_from_db()
    assert installment.paid_amount == Decimal("77777.00")


# --- tara_payment_id conflict ---

def test_conflicting_tara_payment_id_across_attempts_raises_and_credits_neither(service):
    order_a = make_order(email="conflict-a@example.com")
    order_b = make_order(email="conflict-b@example.com")
    attempt_a = create_payable_attempt(order_a.installments.get())
    attempt_b = create_payable_attempt(order_b.installments.get())

    service.apply_verified_success(attempt_a.id, tara_payment_id="shared-payment-id")

    with pytest.raises(PaymentIdConflictError):
        service.apply_verified_success(attempt_b.id, tara_payment_id="shared-payment-id")

    attempt_a.refresh_from_db()
    attempt_b.refresh_from_db()
    assert attempt_a.status == PaymentAttempt.Status.SUCCEEDED  # first credit stands
    assert attempt_b.status != PaymentAttempt.Status.SUCCEEDED  # second is NOT credited
    order_b.installments.get().refresh_from_db()
    assert order_b.installments.get().status != Installment.Status.PAID


def test_concurrent_conflicting_tara_payment_id_is_safe(service):
    """DB-level uniqueness on PaymentAttempt.tara_payment_id (Phase 3) backstops the application-level conflict check."""
    order_a = make_order(email="db-conflict-a@example.com")
    order_b = make_order(email="db-conflict-b@example.com")
    attempt_a = create_payable_attempt(order_a.installments.get())
    attempt_b = create_payable_attempt(order_b.installments.get())

    attempt_a.tara_payment_id = "raced-payment-id"
    attempt_a.save()

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            attempt_b.tara_payment_id = "raced-payment-id"
            attempt_b.save()


# --- Verified failure ---

def test_apply_verified_failure_transitions_attempt_and_installment(service):
    order = make_order(email="failure@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.PENDING)

    service.apply_verified_failure(attempt.id)

    attempt.refresh_from_db()
    installment.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.FAILED
    assert installment.status == Installment.Status.FAILED


def test_verified_failure_does_not_erase_previous_success(service):
    order = make_order(email="no-erase@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)
    service.apply_verified_success(attempt.id)

    service.apply_verified_failure(attempt.id)  # e.g. a stale/late FAILURE check

    attempt.refresh_from_db()
    installment.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.SUCCEEDED  # unchanged
    assert installment.status == Installment.Status.PAID  # unchanged


def test_verified_failure_does_not_cancel_order(service):
    order = make_order(email="no-auto-cancel@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)

    service.apply_verified_failure(attempt.id)

    order.refresh_from_db()
    assert order.status == Order.Status.PENDING  # untouched


# --- Late verified success on a FAILED/EXPIRED attempt (same Tara productId) ---

@pytest.mark.parametrize("terminal_status", [PaymentAttempt.Status.FAILED, PaymentAttempt.Status.EXPIRED])
def test_late_success_reopens_failed_or_expired_attempt(service, terminal_status):
    """Declined first try, then a successful retry on the same Tara link — the verified success must win."""
    order = make_order(email=f"late-success-{terminal_status.lower()}@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)
    if terminal_status == PaymentAttempt.Status.FAILED:
        service.apply_verified_failure(attempt.id)
    else:
        PaymentAttemptService().transition(attempt, PaymentAttempt.Status.EXPIRED)

    updated = service.apply_verified_success(attempt.id, tara_payment_id="pay-late-1")

    installment.refresh_from_db()
    order.refresh_from_db()
    assert updated.status == PaymentAttempt.Status.SUCCEEDED
    assert updated.tara_payment_id == "pay-late-1"
    assert installment.status == Installment.Status.PAID
    assert order.status == Order.Status.COMPLETED
    assert PaymentConfirmation.objects.filter(payment_attempt=attempt).count() == 1


def test_late_success_expires_newer_open_attempt(service):
    order = make_order(email="late-success-newer@example.com")
    installment = order.installments.get()
    old_attempt = create_payable_attempt(installment)
    service.apply_verified_failure(old_attempt.id)
    newer_attempt = create_payable_attempt(installment)  # customer restarted checkout

    service.apply_verified_success(old_attempt.id)

    old_attempt.refresh_from_db()
    newer_attempt.refresh_from_db()
    installment.refresh_from_db()
    assert old_attempt.status == PaymentAttempt.Status.SUCCEEDED
    assert newer_attempt.status == PaymentAttempt.Status.EXPIRED
    assert installment.status == Installment.Status.PAID


def test_late_success_on_already_paid_installment_is_flagged_not_credited(service):
    """Customer paid twice (old link + new link) — never credited twice, never silently dropped."""
    order = make_order(email="late-success-duplicate@example.com")
    installment = order.installments.get()
    old_attempt = create_payable_attempt(installment)
    service.apply_verified_failure(old_attempt.id)
    newer_attempt = create_payable_attempt(installment)
    service.apply_verified_success(newer_attempt.id)

    with pytest.raises(DuplicatePaymentError):
        service.apply_verified_success(old_attempt.id)

    old_attempt.refresh_from_db()
    installment.refresh_from_db()
    assert old_attempt.status == PaymentAttempt.Status.FAILED
    assert installment.paid_amount == installment.expected_amount
    assert PaymentConfirmation.objects.filter(installment=installment).count() == 1


def test_failure_after_late_success_never_overwrites_it(service):
    order = make_order(email="late-success-then-failure@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)
    service.apply_verified_failure(attempt.id)
    service.apply_verified_success(attempt.id)

    service.apply_verified_failure(attempt.id)

    attempt.refresh_from_db()
    installment.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.SUCCEEDED
    assert installment.status == Installment.Status.PAID
