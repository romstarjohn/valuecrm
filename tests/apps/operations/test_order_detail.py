from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission, User
from django.test import Client
from django.urls import reverse

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import PaymentAttempt, PaymentPlan
from apps.payments.services import OrderService, PaymentAttemptService, PaymentCreditService

pytestmark = pytest.mark.django_db


def make_full_order(email="detail@example.com"):
    course = Course.objects.create(cf_course_id=f"crs_{email}", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code=f"plan_{email}", name="Bootcamp", course=course,
        installment_count=1, installment_amount=Decimal("100000.00"), is_active=True,
    )
    contact = Contact.objects.create(email=email, first_name="Detail", last_name="Test")
    order, _ = OrderService().create_order(contact, plan.id, f"idem-{email}")
    installment = order.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    attempt = PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentCreditService().apply_verified_success(attempt.id)
    return order


def detail_url(order):
    return reverse("operations:order_detail", args=[order.reference])


def test_anonymous_redirected(client):
    order = make_full_order("detail-anon@example.com")
    response = client.get(detail_url(order))
    assert response.status_code == 302


def test_staff_without_view_permission_forbidden():
    order = make_full_order("detail-noperm@example.com")
    user = User.objects.create_user(username="detailnoperm", password="x", is_staff=True)
    client = Client()
    client.login(username="detailnoperm", password="x")
    response = client.get(detail_url(order))
    assert response.status_code == 403


def test_authorized_get_succeeds(ops_client):
    order = make_full_order("detail-authorized@example.com")
    response = ops_client.get(detail_url(order))
    assert response.status_code == 200


def test_shows_all_related_workflow_records(ops_client):
    order = make_full_order("detail-full-workflow@example.com")
    response = ops_client.get(detail_url(order))
    assert response.status_code == 200
    assert len(response.context["installments"]) == 1
    assert response.context["order"].pk == order.pk
    # confirmation + provisioning request were created by PaymentCreditService.apply_verified_success
    assert response.context["confirmations"].count() == 1
    assert response.context["provisioning_requests"].count() == 1


def test_links_to_contact_and_course_pages(ops_client):
    order = make_full_order("detail-links@example.com")
    response = ops_client.get(detail_url(order))
    content = response.content.decode()
    assert reverse("contacts:detail", args=[order.customer_id]) in content
    assert reverse("courses:detail", args=[order.course.cf_course_id]) in content


def test_provider_facts_shown_read_only_separate_from_internal_status(ops_client):
    order = make_full_order("detail-provider-facts@example.com")
    response = ops_client.get(detail_url(order))
    content = response.content.decode()
    attempt = order.installments.get().payment_attempts.get()
    assert attempt.tara_product_id in content
    assert "SUCCEEDED" in content.upper()


def test_no_secrets_or_raw_payloads_exposed(ops_client):
    order = make_full_order("detail-no-secret@example.com")
    response = ops_client.get(detail_url(order))
    content = response.content.decode().lower()
    for forbidden in ("api_key", "webhook_secret", "raw_payload", "bearer "):
        assert forbidden not in content


def test_nonexistent_order_returns_404(ops_client):
    import uuid
    response = ops_client.get(reverse("operations:order_detail", args=[uuid.uuid4()]))
    assert response.status_code == 404
