"""Client pages for non-technical staff (docs/UI_VOCABULARY.md)."""
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import PaymentAttempt, PaymentPlan, TaraWebhookEvent
from apps.payments.services import OrderService, PaymentAttemptService, PaymentCreditService

pytestmark = pytest.mark.django_db


def make_sale(contact, key, count=1):
    course = Course.objects.get_or_create(cf_course_id="crs_client", defaults={"name": "Cheveux crépus", "workspace_id": "ws"})[0]
    plan = PaymentPlan.objects.create(
        code=f"plan_{key}", name="Formule", course=course,
        installment_count=count, installment_amount=Decimal("1000.00"), is_active=True,
    )
    order, _ = OrderService().create_order(contact, plan.id, f"idem-{key}")
    attempt = PaymentAttemptService().create_attempt(order.installments.order_by("sequence").first())
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentAttempt.objects.filter(pk=attempt.pk).update(general_link="https://taramoney.com/pay/x")
    attempt.refresh_from_db()
    return order, attempt


def test_client_page_shows_sale_state_and_verify_action(superuser_client):
    contact = Contact.objects.create(email="sarah@example.com", first_name="Sarah", last_name="Nguimgo")
    _, attempt = make_sale(contact, "reported")
    TaraWebhookEvent.objects.create(
        dedup_key="d1", tara_product_id=attempt.tara_product_id, raw_provider_status="SUCCESS",
        processing_status=TaraWebhookEvent.ProcessingStatus.FAILED, payment_attempt=attempt,
    )
    paid, paid_attempt = make_sale(contact, "paid")
    PaymentCreditService().apply_verified_success(paid_attempt.id)

    response = superuser_client.get(reverse("contacts:detail", args=[contact.pk]))
    content = response.content.decode()

    assert response.status_code == 200
    assert "Sarah Nguimgo" in content
    assert "À traiter pour ce client" in content
    assert "À vérifier" in content and "Payée" in content
    assert reverse("operations:payment_attempt_check_status", args=[attempt.pk]) in content
    for tab in ("Résumé", "Achats", "Accès aux formations", "Historique"):
        assert tab in content
    assert "ID ClickFunnels" in content  # still there, but inside "Détails techniques"


def test_client_list_shows_sales_count_and_plain_columns(superuser_client):
    contact = Contact.objects.create(email="list@example.com", first_name="Awa", last_name="B", phone="+237600000000")
    make_sale(contact, "list-1")

    response = superuser_client.get(reverse("contacts:list"))
    content = response.content.decode()

    assert response.status_code == 200
    assert "Clients" in content and "Ajouter un client" in content
    row = next(c for c in response.context["page_obj"] if c.pk == contact.pk)
    assert row.sale_count == 1 and row.open_access_count == 0
    assert "+237600000000" in content


def test_client_form_uses_comma_separated_tags_not_json(superuser_client):
    contact = Contact.objects.create(email="tags@example.com", tags=["VIP"])

    get = superuser_client.get(reverse("contacts:edit", args=[contact.pk]))
    assert 'value="VIP"' in get.content.decode()
    assert "JSON" not in get.content.decode()

    superuser_client.post(reverse("contacts:edit", args=[contact.pk]), {
        "email": "tags@example.com", "first_name": "T", "tags": "VIP, promo rentrée, VIP",
    })
    contact.refresh_from_db()
    assert contact.tags == ["VIP", "promo rentrée"]


def test_client_form_keeps_imported_addresses(superuser_client):
    contact = Contact.objects.create(email="addr@example.com", shipping_address={"city": "Douala"})
    superuser_client.post(reverse("contacts:edit", args=[contact.pk]), {"email": "addr@example.com", "first_name": "A"})
    contact.refresh_from_db()
    assert contact.shipping_address == {"city": "Douala"}


def test_client_panel_renders(superuser_client):
    contact = Contact.objects.create(email="panel@example.com", first_name="P")
    make_sale(contact, "panel")
    response = superuser_client.get(reverse("contacts:detail_panel", args=[contact.pk]))
    assert response.status_code == 200
    assert "En attente de paiement" in response.json()["body_html"]
