from decimal import Decimal

import pytest
from django.urls import reverse

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import PaymentAttempt, PaymentPlan
from apps.payments.services import CheckoutService, OrderService, PaymentAttemptService

pytestmark = pytest.mark.django_db


@pytest.fixture
def order():
    course = Course.objects.create(cf_course_id="crs_1", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code="bootcamp-1x", name="Bootcamp", course=course,
        installment_count=1, installment_amount=Decimal("100000.00"), is_active=True,
    )
    contact = Contact.objects.create(email="buyer@example.com")
    created, _ = OrderService().create_order(contact, plan.id, "idem-status-tests")
    return created


def status_url(token):
    return reverse("payments:checkout_status", args=[token])


def test_valid_signed_reference_resolves_order(client, order):
    token = CheckoutService().build_signed_reference(order.reference)
    response = client.get(status_url(token))
    assert response.status_code == 200
    assert str(order.reference).encode() in response.content


def test_tampered_reference_rejected(client, order):
    token = CheckoutService().build_signed_reference(order.reference)
    tampered = token[:-2] + ("zz" if not token.endswith("zz") else "aa")
    response = client.get(status_url(tampered))
    assert response.status_code == 400
    assert b"couldn't find this order" in response.content or b"invalid" in response.content.lower()


def test_expired_reference_rejected(client, order, mocker):
    """
    django.core.signing.SignatureExpired is deterministic on elapsed wall-clock
    time, so exercising a real expiry here would mean sleeping past
    max_age_seconds (a full day, per resolve_signed_reference's default) —
    tests/services/test_checkout_service.py::test_expired_signed_reference_rejected
    already proves this directly with max_age_seconds=-1. This test instead
    confirms the VIEW handles that same CheckoutError correctly (400, safe
    generic message), by simulating what resolve_signed_reference raises once
    a token has expired.
    """
    from apps.payments.services import CheckoutError

    token = CheckoutService().build_signed_reference(order.reference)
    mocker.patch(
        "apps.payments.services.CheckoutService.resolve_signed_reference",
        side_effect=CheckoutError("This checkout link has expired."),
    )
    response = client.get(status_url(token))
    assert response.status_code == 400
    assert b"expired" in response.content.lower()


def test_query_string_success_ignored(client, order):
    """Appending ?status=success&amount=0&paid=true must have zero effect on what's displayed."""
    token = CheckoutService().build_signed_reference(order.reference)
    response = client.get(status_url(token) + "?status=success&amount=0&paid=true&course=Bootcamp")
    assert response.status_code == 200
    assert b"Payment Confirmed" not in response.content  # nothing was actually paid; query string must not cause this


def test_another_customers_order_rejected_via_wrong_token(client, order):
    """A UUID alone is not authorization — resolving requires the correctly signed token for THIS order, not any other valid-looking token."""
    other_contact = Contact.objects.create(email="other@example.com")
    course2 = Course.objects.create(cf_course_id="crs_2", name="Other Course", workspace_id="ws_1")
    plan2 = PaymentPlan.objects.create(
        code="other-plan", name="Other Plan", course=course2,
        installment_count=1, installment_amount=Decimal("1.00"), is_active=True,
    )
    other_order, _ = OrderService().create_order(other_contact, plan2.id, "idem-other-order")

    token_for_order = CheckoutService().build_signed_reference(order.reference)
    response = client.get(status_url(token_for_order))
    assert str(order.reference).encode() in response.content
    assert str(other_order.reference).encode() not in response.content


def test_bare_uuid_reference_is_rejected(client, order):
    response = client.get(status_url(str(order.reference)))
    assert response.status_code == 400


def test_no_payment_mutation_from_viewing_status_page(client, order):
    installment = order.installments.get()
    original_status = installment.status
    token = CheckoutService().build_signed_reference(order.reference)

    client.get(status_url(token) + "?status=success")
    client.get(status_url(token))

    installment.refresh_from_db()
    order.refresh_from_db()
    assert installment.status == original_status
    assert installment.paid_at is None
    assert installment.paid_amount is None


def test_status_page_response_is_not_cached(client, order):
    token = CheckoutService().build_signed_reference(order.reference)
    response = client.get(status_url(token))
    cache_control = response.get("Cache-Control", "")
    assert "no-cache" in cache_control or "no-store" in cache_control or "private" in cache_control


def test_payment_confirmed_shown_only_after_succeeded_attempt(client, order):
    installment = order.installments.get()
    from apps.payments.services import PaymentAttemptService
    attempt = PaymentAttemptService().create_attempt(installment)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.PENDING)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.SUCCEEDED)

    token = CheckoutService().build_signed_reference(order.reference)
    response = client.get(status_url(token))
    assert b"Payment Confirmed" in response.content


def test_failed_attempt_shows_failed_status(client, order):
    installment = order.installments.get()
    from apps.payments.services import PaymentAttemptService
    attempt = PaymentAttemptService().create_attempt(installment)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.FAILED)

    token = CheckoutService().build_signed_reference(order.reference)
    response = client.get(status_url(token))
    assert b"Payment Failed" in response.content
