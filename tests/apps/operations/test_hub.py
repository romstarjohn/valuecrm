from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission, User
from django.urls import reverse

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import Installment, Order, PaymentAttempt, PaymentPlan
from apps.payments.services import OrderService, PaymentAttemptService, PaymentCreditService

pytestmark = pytest.mark.django_db

HUB_URL = reverse("operations:hub")


def make_order(email, installment_count=1, installment_amount=Decimal("100000.00")):
    course = Course.objects.create(cf_course_id=f"crs_{email}", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code=f"plan_{email}", name="Bootcamp", course=course,
        installment_count=installment_count, installment_amount=installment_amount, is_active=True,
    )
    contact = Contact.objects.create(email=email)
    order, _ = OrderService().create_order(contact, plan.id, f"idem-{email}")
    return order


def create_payable_attempt(installment):
    attempt = PaymentAttemptService().create_attempt(installment)
    return PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)


def test_anonymous_redirected(client):
    response = client.get(HUB_URL)
    assert response.status_code == 302
    assert "/connexion/" in response.url


def test_authorized_access(staff_client):
    response = staff_client.get(HUB_URL)
    assert response.status_code == 200


def test_pending_orders_summary_count(staff_client):
    make_order("hub-pending-1@example.com")
    make_order("hub-pending-2@example.com")
    response = staff_client.get(HUB_URL)
    assert response.context["pending_orders"] == 2


def test_payments_awaiting_verification_summary_count(staff_client):
    order = make_order("hub-pending-verify@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.PENDING)

    response = staff_client.get(HUB_URL)
    assert response.context["payments_awaiting_verification"] == 1


def test_manual_review_summary_count(staff_client):
    order = make_order("hub-manual-review@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.PENDING)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.UNKNOWN)

    response = staff_client.get(HUB_URL)
    assert response.context["manual_review_items"] == 1


def test_pending_orders_summary_links_apply_correct_filter(staff_client):
    response = staff_client.get(HUB_URL)
    assert reverse("operations:order_list") in response.content.decode()


def test_manual_review_link_applies_needs_review_filter(staff_client):
    response = staff_client.get(HUB_URL)
    assert f'{reverse("operations:order_list")}?needs_review=1' in response.content.decode()


def test_recent_verified_payments_shown(staff_client):
    order = make_order("hub-recent-verified@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)
    PaymentCreditService().apply_verified_success(attempt.id)

    response = staff_client.get(HUB_URL)
    assert len(response.context["recent_verified_payments"]) == 1
