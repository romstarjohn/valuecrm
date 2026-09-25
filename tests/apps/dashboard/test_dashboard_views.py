import html
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import Installment, PaymentAttempt, PaymentConfirmation, PaymentPlan, TaraWebhookEvent
from apps.payments.services import OrderService, PaymentAttemptService, PaymentCreditService
from apps.provisioning.models import ProvisioningRequest

pytestmark = pytest.mark.django_db

URL = reverse("dashboard:index")


def make_order(email, first_name="Sarah", last_name="Nguimgo", count=1):
    course, _ = Course.objects.get_or_create(
        cf_course_id="crs_inbox", defaults={"name": "Cheveux Crépus longs et Libres", "workspace_id": "ws_1"},
    )
    plan = PaymentPlan.objects.create(
        code=f"plan-{email}", name="Formule", course=course, installment_count=count,
        installment_amount=Decimal("1000.00"), is_active=True,
    )
    contact = Contact.objects.create(email=email, first_name=first_name, last_name=last_name)
    order, _ = OrderService().create_order(contact, plan.id, f"idem-{email}")
    return order


def with_link(order):
    attempt = PaymentAttemptService().create_attempt(order.installments.order_by("sequence").first())
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentAttempt.objects.filter(pk=attempt.pk).update(general_link="https://taramoney.com/pay/x")
    attempt.refresh_from_db()
    return attempt


def reported_but_unconfirmed(email="sarah@example.com"):
    """The production case: Tara sent SUCCESS, our verification could not confirm it."""
    order = make_order(email)
    attempt = with_link(order)
    TaraWebhookEvent.objects.create(
        dedup_key=f"evt-{email}", tara_product_id=attempt.tara_product_id, raw_provider_status="SUCCESS",
        processing_status=TaraWebhookEvent.ProcessingStatus.FAILED,
        failure_category=TaraWebhookEvent.FailureCategory.PROVIDER_LOOKUP_INDETERMINATE,
        payment_attempt=attempt,
    )
    return order, attempt


def test_requires_login(client):
    response = client.get(URL)
    assert response.status_code == 302
    assert "/connexion/" in response.url


def test_empty_state(superuser_client):
    content = html.unescape(superuser_client.get(URL).content.decode())
    assert "À traiter" in content
    assert "Rien à traiter pour le moment" in content
    assert "Les nouvelles ventes apparaissent dans Ventes." in content


def test_payment_reported_by_tara_comes_first_with_verify_action(superuser_client):
    # A lower-priority item created first must not outrank it.
    overdue = make_order("late@example.com", first_name="Paul", last_name="Mbarga", count=2)
    PaymentCreditService().apply_verified_success(with_link(overdue).id)
    Installment.objects.filter(order=overdue, sequence=2).update(due_date=date.today() - timedelta(days=5))
    order, attempt = reported_but_unconfirmed()

    response = superuser_client.get(URL)
    content = html.unescape(response.content.decode())

    first_section = response.context["sections"][0]
    assert first_section["key"] == "verifier"
    row = first_section["rows"][0]
    assert row["title"] == "Sarah N. — 1 000 XAF — Cheveux Crépus longs et Libres"
    assert row["action_label"] == "Vérifier le paiement"
    assert row["action_url"] == reverse("operations:payment_attempt_check_status", args=[attempt.pk])
    assert content.index('data-inbox-type="verifier"') < content.index('data-inbox-type="retard"')


def test_category_counts_and_every_item_type(superuser_client):
    reported_but_unconfirmed()

    overdue = make_order("late@example.com", first_name="Paul", last_name="Mbarga", count=2)
    PaymentCreditService().apply_verified_success(with_link(overdue).id)
    Installment.objects.filter(order=overdue, sequence=2).update(due_date=date.today() - timedelta(days=3))

    paid = make_order("paid@example.com", first_name="Ada", last_name="Lovelace")
    PaymentCreditService().apply_verified_success(with_link(paid).id)
    PaymentConfirmation.objects.filter(order=paid).update(status=PaymentConfirmation.Status.FAILED)
    ProvisioningRequest.objects.filter(order=paid).update(status=ProvisioningRequest.Status.FAILED)
    if not ProvisioningRequest.objects.filter(order=paid).exists():
        ProvisioningRequest.objects.create(
            order=paid, course=paid.course, contact=paid.customer,
            policy_snapshot=ProvisioningRequest.PolicySnapshot.AUTOMATIC, status=ProvisioningRequest.Status.FAILED,
        )

    TaraWebhookEvent.objects.create(
        dedup_key="uncorrelated-1", tara_product_id="", tara_payment_id="p-9", raw_provider_status="SUCCESS",
        amount=Decimal("5000"), processing_status=TaraWebhookEvent.ProcessingStatus.UNCORRELATED,
    )

    response = superuser_client.get(URL)
    content = html.unescape(response.content.decode())
    counts = {chip["key"]: chip["count"] for chip in response.context["chips"]}

    assert counts == {"verifier": 1, "retard": 1, "acces": 1, "email": 1, "associer": 1}
    assert response.context["to_verify_count"] == 1
    assert response.context["overdue_count"] == 1
    for text in ("Vérifier le paiement", "Voir la vente", "Relancer l'accès", "Renvoyer l'e-mail", "Associer à une vente"):
        assert text in content
    assert "Paiement Tara de 5 000 XAF" in content


def test_type_filter_shows_only_that_category(superuser_client):
    reported_but_unconfirmed()
    TaraWebhookEvent.objects.create(
        dedup_key="uncorrelated-2", raw_provider_status="SUCCESS", tara_payment_id="p-1",
        processing_status=TaraWebhookEvent.ProcessingStatus.UNCORRELATED,
    )

    response = superuser_client.get(URL + "?type=associer")

    assert [s["key"] for s in response.context["sections"]] == ["associer"]


def test_without_permissions_actions_fall_back_safely(staff_client):
    """A plain staff user gets "Voir la vente" instead of an action they may not perform."""
    order, _ = reported_but_unconfirmed()
    TaraWebhookEvent.objects.create(
        dedup_key="uncorrelated-3", raw_provider_status="SUCCESS", tara_payment_id="p-2",
        processing_status=TaraWebhookEvent.ProcessingStatus.UNCORRELATED,
    )

    response = staff_client.get(URL)
    content = html.unescape(response.content.decode())

    row = response.context["sections"][0]["rows"][0]
    assert row["action_label"] == "Voir la vente"
    assert row["action_url"] == reverse("operations:order_detail", args=[order.reference])
    assert "Réservé à l'administrateur" in content


def test_expired_and_paid_sales_are_not_listed(superuser_client):
    paid = make_order("done@example.com")
    PaymentCreditService().apply_verified_success(with_link(paid).id)
    PaymentConfirmation.objects.filter(order=paid).update(status=PaymentConfirmation.Status.SENT)
    ProvisioningRequest.objects.filter(order=paid).update(status=ProvisioningRequest.Status.COMPLETED)
    with_link(make_order("waiting@example.com"))  # link opened, not paid: not an inbox item

    response = superuser_client.get(URL)
    assert response.context["sections"] == []
