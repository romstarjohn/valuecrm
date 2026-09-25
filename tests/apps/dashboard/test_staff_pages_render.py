"""Smoke test: every inbox / access / settings / technical page renders with realistic data."""
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.enrollments.models import EnrollmentAttempt
from apps.payments.models import PaymentAttempt, PaymentConfirmation, PaymentPlan, ReconciliationRun, TaraWebhookEvent
from apps.payments.services import OrderService, PaymentAttemptService, PaymentCreditService
from apps.provisioning.models import ProvisioningRequest

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    course = Course.objects.create(cf_course_id="crs_x", name="Cheveux Crépus longs et Libres", workspace_id="w")
    plan = PaymentPlan.objects.create(code="p1", name="Formule", course=course, installment_count=1,
                                      installment_amount=Decimal("1000"), is_active=True)
    sarah = Contact.objects.create(email="sarah@example.com", first_name="Sarah", last_name="Nguimgo")
    order, _ = OrderService().create_order(sarah, plan.id, "k1")
    attempt = PaymentAttemptService().create_attempt(order.installments.get())
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentAttempt.objects.filter(pk=attempt.pk).update(general_link="https://taramoney.com/pay/x")
    TaraWebhookEvent.objects.create(dedup_key="e1", tara_product_id=attempt.tara_product_id, raw_provider_status="SUCCESS",
                                    processing_status=TaraWebhookEvent.ProcessingStatus.FAILED, payment_attempt=attempt)
    ada = Contact.objects.create(email="ada@example.com", first_name="Ada", last_name="L")
    paid, _ = OrderService().create_order(ada, plan.id, "k2")
    a2 = PaymentAttemptService().create_attempt(paid.installments.get())
    PaymentAttemptService().transition(a2, PaymentAttempt.Status.LINK_CREATED)
    PaymentCreditService().apply_verified_success(a2.id)
    PaymentConfirmation.objects.filter(order=paid).update(status=PaymentConfirmation.Status.FAILED)
    if not ProvisioningRequest.objects.filter(order=paid).exists():
        ProvisioningRequest.objects.create(order=paid, course=course, contact=ada, policy_snapshot="AUTOMATIC")
    ProvisioningRequest.objects.filter(order=paid).update(status=ProvisioningRequest.Status.FAILED)
    TaraWebhookEvent.objects.create(dedup_key="e2", tara_payment_id="p9", raw_provider_status="SUCCESS", amount=Decimal("5000"),
                                    processing_status=TaraWebhookEvent.ProcessingStatus.UNCORRELATED)
    EnrollmentAttempt.objects.create(contact=ada, course=course, status="FAILURE", error_log="401 Unauthorized")
    ok = EnrollmentAttempt.objects.create(contact=sarah, course=course, status="SUCCESS", cf_enrollment_id="e-1")
    ReconciliationRun.objects.create(started_at=order.created_at, run_status="SUCCESS", orders_expired=2)
    return {"attempt": attempt, "enrollment": ok, "event": TaraWebhookEvent.objects.get(dedup_key="e2")}


def test_every_page_renders(superuser_client, world):
    urls = [
        reverse("dashboard:index"), reverse("dashboard:index") + "?type=verifier",
        reverse("enrollments:list"), reverse("enrollments:new"), reverse("enrollments:bulk"),
        reverse("enrollments:detail", args=[world["enrollment"].pk]),
        reverse("enrollments:detail_panel", args=[world["enrollment"].pk]),
        reverse("enrollments:freeze", args=[world["enrollment"].pk]),
        reverse("configuration:settings"), reverse("configuration:tara_settings"),
        reverse("operations:hub"), reverse("operations:installment_list"), reverse("operations:payment_attempt_list"),
        reverse("operations:webhook_event_list"), reverse("operations:webhook_event_attribute", args=[world["event"].pk]),
        reverse("operations:webhook_event_attribute", args=[world["event"].pk]) + "?q=ada",
        reverse("operations:confirmation_list"), reverse("operations:provisioning_list"),
        reverse("operations:reconciliation_list"), reverse("operations:audit_list"),
    ]
    for url in urls:
        response = superuser_client.get(url)
        assert response.status_code == 200, url
    inbox = superuser_client.get(reverse("dashboard:index")).content.decode()
    assert "Vérifier le paiement" in inbox and "Relancer l&#x27;accès" in inbox and "Renvoyer l&#x27;e-mail" in inbox
    tech = superuser_client.get(reverse("operations:webhook_event_list")).content.decode()
    assert "Vente #" in tech and "Sarah Nguimgo" in tech
