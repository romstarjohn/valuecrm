"""Whole-site smoke test after the staff-UI rework: every menu entry renders for a real user journey."""
from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission, User
from django.test import Client

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import PaymentAttempt, PaymentPlan, TaraWebhookEvent
from apps.payments.services import OrderService, PaymentAttemptService

pytestmark = pytest.mark.django_db

MENU_URLS = ["/", "/operations/orders/", "/contacts/", "/enrollments/", "/courses/", "/settings/settings/", "/settings/tara/"]


@pytest.fixture
def sale():
    course = Course.objects.create(cf_course_id="crs_nav", name="Cheveux crépus", workspace_id="ws")
    plan = PaymentPlan.objects.create(code="nav-1x", name="Paiement unique", course=course, installment_count=1,
                                      installment_amount=Decimal("1000.00"), is_active=True)
    contact = Contact.objects.create(email="sarah@example.com", first_name="Sarah", last_name="N.")
    order, _ = OrderService().create_order(contact, plan.id, "nav-key")
    attempt = PaymentAttemptService().create_attempt(order.installments.get())
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    TaraWebhookEvent.objects.create(
        dedup_key="nav", tara_product_id=attempt.tara_product_id, raw_provider_status="SUCCESS",
        processing_status=TaraWebhookEvent.ProcessingStatus.FAILED, payment_attempt=attempt,
    )
    return order, contact, course, attempt


def test_superuser_journey_renders_everything_and_sees_technique(sale):
    order, contact, course, attempt = sale
    client = Client()
    client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))

    for url in MENU_URLS + [f"/operations/orders/{order.reference}/", f"/contacts/{contact.pk}/",
                            f"/courses/{course.cf_course_id}/", "/operations/", "/operations/payment-attempts/"]:
        response = client.get(url)
        assert response.status_code == 200, url

    inbox = client.get("/").content.decode()
    assert "Vérifier le paiement" in inbox and f"/payment-attempts/{attempt.pk}/check-status/" in inbox
    assert "Outils techniques" in inbox


def test_staff_without_technical_rights_does_not_see_technique(sale):
    user = User.objects.create_user("staff", password="x", is_staff=True)
    user.user_permissions.add(*Permission.objects.filter(codename__in=["view_order", "view_contact"]))
    client = Client()
    client.force_login(user)

    page = client.get("/operations/orders/").content.decode()

    assert "Ventes" in page and "Clients" in page and "Offres" in page
    assert "Outils techniques" not in page
