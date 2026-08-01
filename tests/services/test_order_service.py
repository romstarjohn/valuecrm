from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import Installment, Order, PaymentPlan
from apps.payments.services import (
    InstallmentService,
    InvalidStateTransitionError,
    OrderCreationError,
    OrderService,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def course():
    return Course.objects.create(cf_course_id="crs_1", name="Bootcamp", workspace_id="ws_1")


@pytest.fixture
def other_course():
    return Course.objects.create(cf_course_id="crs_2", name="Advanced Bootcamp", workspace_id="ws_1")


@pytest.fixture
def plan(course):
    return PaymentPlan.objects.create(
        code="bootcamp-3x", name="Bootcamp — 3 installments", course=course,
        currency=PaymentPlan.Currency.XAF, installment_count=3, installment_amount=Decimal("34000.00"),
        installment_interval_days=30, access_policy=PaymentPlan.AccessPolicy.FULL_PAYMENT, is_active=True,
    )


@pytest.fixture
def contact():
    return Contact.objects.create(email="buyer@example.com", phone="+15550001111")


@pytest.fixture
def service():
    return OrderService()


# --- Successful creation + snapshot correctness ---

def test_successful_order_creation_from_active_plan(service, contact, plan):
    order, created = service.create_order(contact, plan.id, "idem-1")
    assert created is True
    assert order.customer_id == contact.id
    assert order.plan_id == plan.id
    assert order.status == Order.Status.PENDING


def test_complete_plan_and_course_snapshot(service, contact, plan, course):
    order, _ = service.create_order(contact, plan.id, "idem-1")
    assert order.plan_code == plan.code
    assert order.plan_name == plan.name
    assert order.course_id == course.id
    assert order.course_cf_id == course.cf_course_id
    assert order.course_name == course.name
    assert order.currency == plan.currency
    assert order.installment_count == plan.installment_count
    assert order.installment_amount == plan.installment_amount
    assert order.installment_interval_days == plan.installment_interval_days
    assert order.access_policy == plan.access_policy
    assert order.total_expected_amount == plan.computed_total


def test_total_equals_snapshotted_schedule(service, contact, plan):
    order, _ = service.create_order(contact, plan.id, "idem-1")
    assert order.total_expected_amount == order.installment_count * order.installment_amount
    schedule_sum = sum((i.expected_amount for i in order.installments.all()), Decimal("0"))
    assert schedule_sum == order.total_expected_amount


# --- Rejections ---

def test_inactive_plan_is_rejected(service, contact, plan):
    plan.is_active = False
    plan.save()
    with pytest.raises(OrderCreationError):
        service.create_order(contact, plan.id, "idem-1")
    assert not Order.objects.exists()


def test_missing_plan_is_rejected(service, contact):
    with pytest.raises(OrderCreationError):
        service.create_order(contact, 999999, "idem-1")
    assert not Order.objects.exists()


# --- Editing the plan after order creation must not change the order ---

def test_plan_price_edit_does_not_change_existing_order(service, contact, plan):
    order, _ = service.create_order(contact, plan.id, "idem-1")
    original_amount = order.installment_amount
    original_total = order.total_expected_amount

    plan.installment_amount = Decimal("99999.00")
    plan.save()

    order.refresh_from_db()
    assert order.installment_amount == original_amount
    assert order.total_expected_amount == original_total
    for installment in order.installments.all():
        assert installment.expected_amount == original_amount


def test_plan_course_edit_does_not_change_existing_order(service, contact, plan, course, other_course):
    order, _ = service.create_order(contact, plan.id, "idem-1")

    plan.course = other_course
    plan.save()

    order.refresh_from_db()
    assert order.course_id == course.id
    assert order.course_cf_id == course.cf_course_id
    assert order.course_name == course.name


def test_plan_access_policy_edit_does_not_change_existing_order(service, contact, plan):
    order, _ = service.create_order(contact, plan.id, "idem-1")
    assert order.access_policy == PaymentPlan.AccessPolicy.FULL_PAYMENT

    plan.access_policy = PaymentPlan.AccessPolicy.FIRST_INSTALLMENT
    plan.save()

    order.refresh_from_db()
    assert order.access_policy == PaymentPlan.AccessPolicy.FULL_PAYMENT


# --- Deterministic installments ---

def test_deterministic_installment_count_and_amounts(service, contact, plan):
    order, _ = service.create_order(contact, plan.id, "idem-1")
    installments = list(order.installments.order_by("sequence"))
    assert len(installments) == 3
    for installment in installments:
        assert installment.expected_amount == Decimal("34000.00")
        assert installment.currency == plan.currency
        assert installment.status == Installment.Status.SCHEDULED


def test_deterministic_installment_due_dates(service, contact, plan):
    start = date(2026, 1, 1)
    order, _ = service.create_order(contact, plan.id, "idem-1", start_date=start)
    installments = list(order.installments.order_by("sequence"))
    assert installments[0].due_date == start
    assert installments[1].due_date == start + timedelta(days=30)
    assert installments[2].due_date == start + timedelta(days=60)


def test_unique_installment_sequence_enforced_at_db_level(service, contact, plan):
    order, _ = service.create_order(contact, plan.id, "idem-1")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Installment.objects.create(
                order=order, sequence=1, expected_amount=Decimal("1.00"),
                currency="XAF", due_date=date.today(),
            )


def test_positive_amount_constraint_on_installment(service, contact, plan):
    order, _ = service.create_order(contact, plan.id, "idem-1")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Installment.objects.create(
                order=order, sequence=99, expected_amount=Decimal("0.00"),
                currency="XAF", due_date=date.today(),
            )


def test_positive_amount_constraint_on_order(service, contact, plan):
    order, _ = service.create_order(contact, plan.id, "idem-1")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Order.objects.filter(pk=order.pk).update(total_expected_amount=Decimal("-1.00"))


# --- Idempotency ---

def test_same_idempotency_key_returns_same_order(service, contact, plan):
    order1, created1 = service.create_order(contact, plan.id, "idem-1")
    order2, created2 = service.create_order(contact, plan.id, "idem-1")
    assert order1.id == order2.id
    assert created1 is True
    assert created2 is False
    assert Order.objects.count() == 1


def test_conflicting_idempotency_key_reuse_is_rejected(service, contact, plan):
    service.create_order(contact, plan.id, "idem-1")
    other_contact = Contact.objects.create(email="other@example.com")
    with pytest.raises(OrderCreationError):
        service.create_order(other_contact, plan.id, "idem-1")
    assert Order.objects.count() == 1


# --- State transitions ---

def test_valid_order_transition_succeeds(service, contact, plan):
    order, _ = service.create_order(contact, plan.id, "idem-1")
    updated = service.transition(order, Order.Status.ACTIVE)
    assert updated.status == Order.Status.ACTIVE


def test_invalid_order_transition_is_rejected(service, contact, plan):
    order, _ = service.create_order(contact, plan.id, "idem-1")
    with pytest.raises(InvalidStateTransitionError):
        service.transition(order, Order.Status.COMPLETED)  # PENDING -> COMPLETED is not allowed directly


def test_order_transition_is_idempotent_for_same_status(service, contact, plan):
    order, _ = service.create_order(contact, plan.id, "idem-1")
    result = service.transition(order, Order.Status.PENDING)
    assert result.status == Order.Status.PENDING


def test_terminal_order_status_rejects_further_transitions(service, contact, plan):
    order, _ = service.create_order(contact, plan.id, "idem-1")
    service.cancel_order(order)
    order.refresh_from_db()
    with pytest.raises(InvalidStateTransitionError):
        service.transition(order, Order.Status.ACTIVE)


def test_invalid_installment_transition_is_rejected(service, contact, plan):
    order, _ = service.create_order(contact, plan.id, "idem-1")
    installment = order.installments.order_by("sequence").first()
    installment_service = InstallmentService()
    installment_service.transition(installment, Installment.Status.PAID)
    with pytest.raises(InvalidStateTransitionError):
        installment_service.transition(installment, Installment.Status.CANCELLED)


def test_paid_installment_cannot_become_cancelled(service, contact, plan):
    """"a provider-confirmed successful payment cannot later become cancelled" — enforced at the Installment level."""
    order, _ = service.create_order(contact, plan.id, "idem-1")
    installment = order.installments.order_by("sequence").first()
    installment_service = InstallmentService()
    installment_service.transition(installment, Installment.Status.PAID, paid_amount=installment.expected_amount)

    with pytest.raises(InvalidStateTransitionError):
        installment_service.transition(installment, Installment.Status.CANCELLED)

    installment.refresh_from_db()
    assert installment.status == Installment.Status.PAID


# --- Cancellation preserves payment history ---

def test_order_cancellation_preserves_paid_installments(service, contact, plan):
    order, _ = service.create_order(contact, plan.id, "idem-1")
    installment = order.installments.order_by("sequence").first()
    InstallmentService().transition(installment, Installment.Status.PAID, paid_amount=installment.expected_amount)

    service.cancel_order(order)

    installment.refresh_from_db()
    order.refresh_from_db()
    assert order.status == Order.Status.CANCELLED
    assert installment.status == Installment.Status.PAID
    assert installment.paid_amount == installment.expected_amount


# --- Deletion protection ---

def test_deleting_course_referenced_by_order_is_protected(service, contact, plan, course):
    service.create_order(contact, plan.id, "idem-1")
    with pytest.raises(ProtectedError):
        course.delete()


def test_deleting_plan_referenced_by_order_is_protected(service, contact, plan):
    service.create_order(contact, plan.id, "idem-1")
    with pytest.raises(ProtectedError):
        plan.delete()


def test_deleting_customer_referenced_by_order_is_protected(service, contact, plan):
    service.create_order(contact, plan.id, "idem-1")
    with pytest.raises(ProtectedError):
        contact.delete()
