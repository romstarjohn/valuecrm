from decimal import Decimal

import pytest

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.operations.presentation import attempt_to_verify, sale_state, short_ref
from apps.payments.models import Order, PaymentAttempt, PaymentPlan, TaraWebhookEvent
from apps.payments.services import OrderService, PaymentAttemptService, PaymentCreditService

pytestmark = pytest.mark.django_db


def make_order(key="k", count=1):
    course = Course.objects.get_or_create(cf_course_id="c1", defaults={"name": "Bootcamp", "workspace_id": "w"})[0]
    plan = PaymentPlan.objects.get_or_create(
        code=f"p{count}", defaults=dict(name="Bootcamp", course=course, installment_count=count,
                                        installment_amount=Decimal("1000.00"), is_active=True),
    )[0]
    contact = Contact.objects.get_or_create(email="c@example.com")[0]
    order, _ = OrderService().create_order(contact, plan.id, key)
    return order


def with_link(order):
    attempt = PaymentAttemptService().create_attempt(order.installments.order_by("sequence").first())
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentAttempt.objects.filter(pk=attempt.pk).update(general_link="https://taramoney.com/pay/x")
    attempt.refresh_from_db()
    return attempt


def test_short_ref():
    assert short_ref("b40d3341-8795-4a50-ac30-6519811c25e6") == "#B40D3341"


def test_not_started_and_awaiting_payment():
    order = make_order("a")
    assert sale_state(order).key == "not_started"
    with_link(order)
    assert sale_state(order).key == "awaiting_payment"


def test_tara_reported_but_unconfirmed_is_to_verify_with_action():
    """The production case: a SUCCESS webhook we could not verify must never look like a normal pending sale."""
    order = make_order("b")
    attempt = with_link(order)
    TaraWebhookEvent.objects.create(
        dedup_key="d", tara_product_id=attempt.tara_product_id, raw_provider_status="SUCCESS",
        processing_status=TaraWebhookEvent.ProcessingStatus.FAILED, payment_attempt=attempt,
    )

    state = sale_state(order)

    assert state.key == "to_verify" and state.needs_attention
    assert state.action_label == "Vérifier le paiement"
    assert f"/payment-attempts/{attempt.pk}/check-status/" in state.action_url
    assert attempt_to_verify(order, {attempt.tara_product_id}) == attempt


def test_paid_and_partial():
    order = make_order("c")
    PaymentCreditService().apply_verified_success(with_link(order).id)
    order.refresh_from_db()
    assert sale_state(order).label == "Payée"

    multi = make_order("d", count=3)
    PaymentCreditService().apply_verified_success(with_link(multi).id)
    multi.refresh_from_db()
    state = sale_state(multi)
    assert state.key == "in_progress" and "1 versement payé sur 3" in state.explanation


def test_expired_and_cancelled_need_no_action():
    order = make_order("e")
    Order.objects.filter(pk=order.pk).update(status=Order.Status.EXPIRED)
    order.refresh_from_db()
    state = sale_state(order)
    assert state.label == "Expirée" and not state.needs_attention and not state.action_url


def test_overdue_installment_is_late_by_due_date():
    from datetime import timedelta
    from django.utils import timezone
    from apps.payments.models import Installment

    multi = make_order("f", count=2)
    PaymentCreditService().apply_verified_success(with_link(multi).id)
    Installment.objects.filter(order=multi, sequence=2).update(due_date=timezone.localdate() - timedelta(days=3))
    multi.refresh_from_db()

    state = sale_state(multi)
    assert state.key == "late" and state.needs_attention
