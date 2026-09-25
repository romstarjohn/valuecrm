from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import Mock

import pytest
from django.core.management import call_command
from django.test import Client
from django.contrib.auth.models import User
from django.utils import timezone

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import Installment, Order, PaymentAttempt, PaymentPlan, TaraWebhookEvent
from apps.payments.services import (
    CheckoutService,
    OrderExpiryService,
    OrderService,
    PaymentAttemptService,
    PaymentCreditService,
)
from integrations.payments.tara.schemas import TaraPaymentLinkResponse

pytestmark = pytest.mark.django_db


@pytest.fixture
def plan():
    course = Course.objects.create(cf_course_id="crs_exp", name="Bootcamp", workspace_id="ws_1")
    return PaymentPlan.objects.create(
        code="exp-1x", name="Bootcamp", course=course, installment_count=1,
        installment_amount=Decimal("1000.00"), is_active=True,
    )


@pytest.fixture
def contact():
    return Contact.objects.create(email="expiry@example.com")


def make_order(contact, plan, key, age, link=True, attempt_status=PaymentAttempt.Status.LINK_CREATED):
    order, _ = OrderService().create_order(contact, plan.id, key)
    attempt = PaymentAttemptService().create_attempt(order.installments.get())
    if attempt_status != PaymentAttempt.Status.CREATED:
        PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED if link else PaymentAttempt.Status.FAILED)
    if link:
        PaymentAttempt.objects.filter(pk=attempt.pk).update(general_link="https://taramoney.com/pay/x")
        if attempt_status not in (PaymentAttempt.Status.LINK_CREATED, PaymentAttempt.Status.CREATED):
            PaymentAttemptService().transition(attempt, attempt_status)
    Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - age)
    order.refresh_from_db()
    attempt.refresh_from_db()
    return order, attempt


# --- Expiry rules ---

def test_order_without_payment_link_expires_after_short_grace(contact, plan):
    old, _ = make_order(contact, plan, "k1", timedelta(hours=2), link=False)
    fresh, _ = make_order(contact, plan, "k2", timedelta(minutes=10), link=False)

    assert OrderExpiryService().expire_stale_orders() == 1

    old.refresh_from_db()
    fresh.refresh_from_db()
    assert old.status == Order.Status.EXPIRED
    assert fresh.status == Order.Status.PENDING


def test_unpaid_order_with_link_expires_after_expiry_days(contact, plan, settings):
    settings.ORDER_PENDING_EXPIRY_DAYS = 7
    old, old_attempt = make_order(contact, plan, "k3", timedelta(days=8))
    recent, _ = make_order(contact, plan, "k4", timedelta(days=2))

    OrderExpiryService().expire_stale_orders()

    old.refresh_from_db()
    old_attempt.refresh_from_db()
    recent.refresh_from_db()
    assert old.status == Order.Status.EXPIRED
    assert old_attempt.status == PaymentAttempt.Status.EXPIRED
    assert old.installments.get().status == Installment.Status.SCHEDULED  # untouched — still payable
    assert recent.status == Order.Status.PENDING


def test_order_with_unresolved_webhook_is_never_expired(contact, plan):
    """Orders 5/6 in production: a SUCCESS webhook that could not be verified must stay visible."""
    order, attempt = make_order(contact, plan, "k5", timedelta(days=30))
    TaraWebhookEvent.objects.create(
        dedup_key="w1", tara_product_id=attempt.tara_product_id, raw_provider_status="SUCCESS",
        processing_status=TaraWebhookEvent.ProcessingStatus.FAILED, payment_attempt=attempt,
    )

    assert OrderExpiryService().expire_stale_orders() == 0
    order.refresh_from_db()
    assert order.status == Order.Status.PENDING


def test_order_with_indeterminate_attempt_is_never_expired(contact, plan):
    order, _ = make_order(contact, plan, "k6", timedelta(days=30), attempt_status=PaymentAttempt.Status.UNKNOWN)

    assert OrderExpiryService().expire_stale_orders() == 0
    order.refresh_from_db()
    assert order.status == Order.Status.PENDING


def test_dry_run_changes_nothing(contact, plan):
    order, _ = make_order(contact, plan, "k7", timedelta(hours=3), link=False)

    out = StringIO()
    call_command("expire_stale_orders", dry_run=True, stdout=out)

    assert f"would expire {order.reference}" in out.getvalue()
    order.refresh_from_db()
    assert order.status == Order.Status.PENDING


# --- Late payment after expiry ---

def test_verified_late_payment_reactivates_expired_order(contact, plan):
    order, attempt = make_order(contact, plan, "k8", timedelta(days=10))
    OrderExpiryService().expire_stale_orders()

    PaymentCreditService().apply_verified_success(attempt.id, tara_payment_id="late-1")

    order.refresh_from_db()
    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.SUCCEEDED
    assert order.installments.get().status == Installment.Status.PAID
    assert order.status == Order.Status.COMPLETED


# --- Checkout: reuse and reopen ---

@pytest.fixture
def tara(mocker):
    client = Mock()
    client.create_payment_link.return_value = TaraPaymentLinkResponse.model_validate(
        {"status": "success", "message": "ok", "generalLink": "https://taramoney.com/pay/abc"},
    )
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=client)
    return client


def test_restarting_checkout_reuses_open_unpaid_order(contact, plan, tara):
    first = CheckoutService().start_checkout(contact, plan.id, "visit-1")
    second = CheckoutService().start_checkout(contact, plan.id, "visit-2")

    assert first.order_reference == second.order_reference
    assert Order.objects.filter(customer=contact).count() == 1
    assert second.checkout_url == "https://taramoney.com/pay/abc"
    assert tara.create_payment_link.call_count == 1  # same Tara link, not a second one


def test_price_change_creates_a_new_order_instead_of_reusing(contact, plan, tara):
    CheckoutService().start_checkout(contact, plan.id, "visit-1")
    PaymentPlan.objects.filter(pk=plan.pk).update(installment_amount=Decimal("1500.00"))

    CheckoutService().start_checkout(contact, plan.id, "visit-2")

    assert Order.objects.filter(customer=contact).count() == 2


def test_paid_order_is_never_reused(contact, plan, tara):
    CheckoutService().start_checkout(contact, plan.id, "visit-1")
    attempt = PaymentAttempt.objects.get(installment__order__customer=contact)
    PaymentCreditService().apply_verified_success(attempt.id)

    CheckoutService().start_checkout(contact, plan.id, "visit-2")

    assert Order.objects.filter(customer=contact).count() == 2


def test_other_customer_never_gets_the_order(contact, plan, tara):
    CheckoutService().start_checkout(contact, plan.id, "visit-1")
    other = Contact.objects.create(email="other@example.com")

    CheckoutService().start_checkout(other, plan.id, "visit-2")

    assert Order.objects.filter(customer=other).count() == 1
    assert Order.objects.count() == 2


def test_expired_order_is_reopened_by_its_own_checkout(contact, plan, tara):
    result = CheckoutService().start_checkout(contact, plan.id, "visit-1")
    Order.objects.filter(reference=result.order_reference).update(status=Order.Status.EXPIRED)

    CheckoutService().start_checkout(contact, plan.id, "visit-1")  # same idempotency key (old tab)

    assert Order.objects.get(reference=result.order_reference).status == Order.Status.PENDING


# --- Operations list ---

def test_order_list_hides_expired_by_default(contact, plan):
    order, _ = make_order(contact, plan, "k9", timedelta(hours=3), link=False)
    OrderExpiryService().expire_stale_orders()
    user = User.objects.create_superuser(username="ops", email="ops@example.com", password="x")
    client = Client()
    client.force_login(user)

    default = client.get("/operations/orders/").content.decode()
    filtered = client.get("/operations/orders/?status=EXPIRED").content.decode()

    assert str(order.reference) not in default
    assert str(order.reference) in filtered
