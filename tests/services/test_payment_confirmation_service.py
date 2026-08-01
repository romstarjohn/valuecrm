from decimal import Decimal

import pytest

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import Installment, Order, PaymentAttempt, PaymentConfirmation, PaymentPlan
from apps.payments.services import (
    OrderService,
    PaymentAttemptService,
    PaymentConfirmationService,
    PaymentCreditService,
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
    contact = Contact.objects.create(email=email, first_name="Ada", last_name="Lovelace")
    order, _ = OrderService().create_order(contact, plan.id, f"idem-{email}")
    return order


def create_payable_attempt(installment: Installment) -> PaymentAttempt:
    attempt = PaymentAttemptService().create_attempt(installment)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    return attempt


def test_create_confirmation_persists_expected_fields():
    order = make_order(email="confirm-fields@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)
    PaymentCreditService().apply_verified_success(attempt.id)
    installment.refresh_from_db()
    order.refresh_from_db()

    confirmation = PaymentConfirmationService().create_confirmation(attempt, installment, order)

    assert confirmation.status == PaymentConfirmation.Status.PENDING
    assert confirmation.channel == PaymentConfirmation.Channel.EMAIL
    assert confirmation.contact_id == order.customer_id
    assert confirmation.order_id == order.id
    assert confirmation.installment_id == installment.id
    assert confirmation.payment_attempt_id == attempt.id
    assert confirmation.recipient_snapshot == order.customer.email
    assert confirmation.idempotency_key


def test_create_confirmation_is_idempotent_per_attempt():
    order = make_order(email="confirm-idempotent@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)
    PaymentCreditService().apply_verified_success(attempt.id)

    service = PaymentConfirmationService()
    first = service.create_confirmation(attempt, installment, order)
    second = service.create_confirmation(attempt, installment, order)

    assert first.id == second.id
    assert PaymentConfirmation.objects.filter(payment_attempt=attempt).count() == 1


def test_recipient_snapshot_is_frozen_at_creation():
    order = make_order(email="snapshot-frozen@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)
    PaymentCreditService().apply_verified_success(attempt.id)

    confirmation = PaymentConfirmationService().create_confirmation(attempt, installment, order)
    assert confirmation.recipient_snapshot == "snapshot-frozen@example.com"

    order.customer.email = "changed-later@example.com"
    order.customer.save(update_fields=["email"])

    confirmation.refresh_from_db()
    assert confirmation.recipient_snapshot == "snapshot-frozen@example.com"  # unchanged


# --- follow-up creation via PaymentCreditService (integration) ---

def test_apply_verified_success_creates_confirmation_on_first_credit():
    order = make_order(email="followup-confirm@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)

    PaymentCreditService().apply_verified_success(attempt.id)

    assert PaymentConfirmation.objects.filter(payment_attempt=attempt).count() == 1


def test_duplicate_verified_success_does_not_duplicate_confirmation():
    order = make_order(email="followup-confirm-dup@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)

    PaymentCreditService().apply_verified_success(attempt.id)
    PaymentCreditService().apply_verified_success(attempt.id)  # duplicate — idempotent no-op

    assert PaymentConfirmation.objects.filter(payment_attempt=attempt).count() == 1


def test_multi_installment_order_creates_one_confirmation_per_installment():
    order = make_order(installment_count=2, installment_amount=Decimal("50000.00"), email="followup-multi@example.com")
    installments = list(order.installments.order_by("sequence"))

    for installment in installments:
        attempt = create_payable_attempt(installment)
        PaymentCreditService().apply_verified_success(attempt.id)

    assert PaymentConfirmation.objects.filter(order=order).count() == 2


# --- Security: no sensitive content stored on the model ---

def test_confirmation_model_stores_no_sensitive_fields():
    order = make_order(email="no-sensitive@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)
    PaymentCreditService().apply_verified_success(attempt.id)

    confirmation = PaymentConfirmation.objects.get(payment_attempt=attempt)
    field_names = {f.name for f in PaymentConfirmation._meta.get_fields()}
    forbidden = {"api_key", "webhook_secret", "raw_payload", "raw_provider_status", "payment_url", "tara_product_id"}
    assert not (forbidden & field_names)
