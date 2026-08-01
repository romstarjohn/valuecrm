from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import Installment, Order, PaymentAttempt, PaymentPlan
from apps.payments.services import InvalidStateTransitionError, OrderService, PaymentAttemptService

pytestmark = pytest.mark.django_db


@pytest.fixture
def installment():
    course = Course.objects.create(cf_course_id="crs_1", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code="bootcamp-1x", name="Bootcamp", course=course, installment_count=1,
        installment_amount=Decimal("100000.00"), is_active=True,
    )
    contact = Contact.objects.create(email="buyer@example.com")
    order, _ = OrderService().create_order(contact, plan.id, "idem-attempt-tests")
    return order.installments.get()


@pytest.fixture
def second_installment():
    """A separate installment (different order) for tests that need two independent installments, not two attempts racing on the same one."""
    course = Course.objects.create(cf_course_id="crs_2", name="Bootcamp 2", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code="bootcamp-1x-b", name="Bootcamp 2", course=course, installment_count=1,
        installment_amount=Decimal("100000.00"), is_active=True,
    )
    contact = Contact.objects.create(email="buyer2@example.com")
    order, _ = OrderService().create_order(contact, plan.id, "idem-attempt-tests-2")
    return order.installments.get()


@pytest.fixture
def service():
    return PaymentAttemptService()


def test_create_attempt_generates_unique_product_id(service, installment):
    attempt = service.create_attempt(installment)
    assert attempt.tara_product_id
    assert attempt.status == PaymentAttempt.Status.CREATED
    assert attempt.expected_amount == installment.expected_amount
    assert attempt.currency == installment.currency
    # Non-sensitive: no PII in the generated identifier.
    assert "buyer" not in attempt.tara_product_id
    assert "@" not in attempt.tara_product_id
    assert "Bootcamp" not in attempt.tara_product_id


def test_tara_product_id_is_unique_at_db_level(service, installment):
    first = service.create_attempt(installment)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            PaymentAttempt.objects.create(
                installment=installment, tara_product_id=first.tara_product_id,
                expected_amount=Decimal("1.00"), currency="XAF",
            )


def test_multiple_attempts_allowed_for_one_installment(service, installment):
    """
    Multiple attempts are allowed SEQUENTIALLY, not simultaneously-active — see
    Phase 5's one_active_payment_attempt_per_installment constraint
    (apps/payments/models.py::PaymentAttempt.Meta). The first must reach a
    terminal, retryable state (FAILED/EXPIRED) before a second may be created;
    this is what a real retry-after-failure looks like, not an artifact of the
    test.
    """
    first = service.create_attempt(installment)
    service.transition(first, PaymentAttempt.Status.FAILED)
    second = service.create_attempt(installment)
    assert first.id != second.id
    assert first.tara_product_id != second.tara_product_id
    assert installment.payment_attempts.count() == 2


def test_null_tara_payment_id_does_not_violate_uniqueness(service, installment, second_installment):
    """Postgres unique indexes permit multiple NULLs — two attempts (on different installments) with no known paymentId yet must coexist."""
    first = service.create_attempt(installment)
    second = service.create_attempt(second_installment)
    assert first.tara_payment_id is None
    assert second.tara_payment_id is None  # no IntegrityError


def test_non_null_tara_payment_id_is_unique_at_db_level(service, installment, second_installment):
    first = service.create_attempt(installment)
    first.tara_payment_id = "tara-payment-123"
    first.save()

    second = service.create_attempt(second_installment)
    second.tara_payment_id = "tara-payment-123"
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            second.save()


def test_positive_amount_constraint_on_payment_attempt(installment):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            PaymentAttempt.objects.create(
                installment=installment, expected_amount=Decimal("0.00"), currency="XAF",
            )


# --- Provider status separated from internal status ---

def test_provider_status_is_separate_from_internal_status(service, installment):
    attempt = service.create_attempt(installment)
    updated = service.transition(
        attempt, PaymentAttempt.Status.LINK_CREATED,
        raw_provider_status="success", provider_message="API_ORDER_SUCESSFULL",
    )
    assert updated.status == PaymentAttempt.Status.LINK_CREATED
    assert updated.raw_provider_status == "success"  # Tara's raw string, uninterpreted
    assert updated.provider_message == "API_ORDER_SUCESSFULL"
    assert updated.initiated_at is not None


# --- State transitions ---

def test_valid_attempt_transition_succeeds(service, installment):
    attempt = service.create_attempt(installment)
    updated = service.transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    assert updated.status == PaymentAttempt.Status.LINK_CREATED


def test_invalid_attempt_transition_is_rejected(service, installment):
    attempt = service.create_attempt(installment)
    with pytest.raises(InvalidStateTransitionError):
        service.transition(attempt, PaymentAttempt.Status.SUCCEEDED)  # CREATED -> SUCCEEDED skips LINK_CREATED/PENDING


def test_transition_is_idempotent_for_same_status(service, installment):
    attempt = service.create_attempt(installment)
    result = service.transition(attempt, PaymentAttempt.Status.CREATED)
    assert result.status == PaymentAttempt.Status.CREATED


def test_final_success_cannot_become_anything_else(service, installment):
    """"a provider-confirmed successful payment cannot later become cancelled" — SUCCEEDED is fully terminal for PaymentAttempt."""
    attempt = service.create_attempt(installment)
    service.transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    service.transition(attempt, PaymentAttempt.Status.PENDING)
    succeeded = service.transition(attempt, PaymentAttempt.Status.SUCCEEDED)
    assert succeeded.completed_at is not None

    for target in [PaymentAttempt.Status.FAILED, PaymentAttempt.Status.EXPIRED, PaymentAttempt.Status.UNKNOWN]:
        with pytest.raises(InvalidStateTransitionError):
            service.transition(succeeded, target)

    succeeded.refresh_from_db()
    assert succeeded.status == PaymentAttempt.Status.SUCCEEDED


def test_final_success_transition_replay_is_idempotent_noop(service, installment):
    """Duplicate/replayed webhook delivering the same final status again must not error — final transitions are idempotent."""
    attempt = service.create_attempt(installment)
    service.transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    service.transition(attempt, PaymentAttempt.Status.PENDING)
    service.transition(attempt, PaymentAttempt.Status.SUCCEEDED)

    replayed = service.transition(attempt, PaymentAttempt.Status.SUCCEEDED)
    assert replayed.status == PaymentAttempt.Status.SUCCEEDED


# --- Deletion protection ---

def test_deleting_installment_with_attempts_is_protected(service, installment):
    service.create_attempt(installment)
    with pytest.raises(ProtectedError):
        installment.delete()
