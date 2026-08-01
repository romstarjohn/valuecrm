from decimal import Decimal

import pytest
from django.urls import reverse

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import PaymentAttempt, PaymentConfirmation, PaymentPlan
from apps.payments.services import OrderService, PaymentAttemptService, PaymentConfirmationService, PaymentCreditService

pytestmark = pytest.mark.django_db

CHANGELIST_URL = reverse("admin:payments_paymentconfirmation_changelist")


@pytest.fixture
def confirmation():
    course = Course.objects.create(cf_course_id="crs_admin_confirm", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code="admin-confirm-plan", name="Bootcamp", course=course,
        installment_count=1, installment_amount=Decimal("100000.00"), is_active=True,
    )
    contact = Contact.objects.create(email="admin-confirm@example.com")
    order, _ = OrderService().create_order(contact, plan.id, "idem-admin-confirm")
    installment = order.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentCreditService().apply_verified_success(attempt.id)
    return PaymentConfirmation.objects.get(payment_attempt=attempt)


def test_anonymous_cannot_view(client, confirmation):
    response = client.get(CHANGELIST_URL)
    assert response.status_code == 302
    assert "/admin/login/" in response.url


def test_staff_without_permissions_cannot_view(staff_client, confirmation):
    response = staff_client.get(CHANGELIST_URL)
    assert response.status_code == 403


def test_superuser_can_view_read_only(superuser_client, confirmation):
    response = superuser_client.get(CHANGELIST_URL)
    assert response.status_code == 200
    assert str(confirmation.reference).encode() in response.content


def test_add_view_not_permitted(superuser_client):
    response = superuser_client.get(reverse("admin:payments_paymentconfirmation_add"))
    assert response.status_code == 403


def test_change_view_does_not_accept_writes(superuser_client, confirmation):
    response = superuser_client.post(
        reverse("admin:payments_paymentconfirmation_change", args=[confirmation.pk]),
        data={"status": PaymentConfirmation.Status.SENT},
    )
    assert response.status_code == 403


def test_delete_view_not_permitted(superuser_client, confirmation):
    response = superuser_client.get(reverse("admin:payments_paymentconfirmation_delete", args=[confirmation.pk]))
    assert response.status_code == 403
