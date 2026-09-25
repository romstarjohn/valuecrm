"""
Ventes pages for non-technical staff (docs/UI_VOCABULARY.md): plain states,
the "À vérifier" filter, the single primary action, plain action wording.
"""
from decimal import Decimal
from unittest.mock import Mock

import pytest
from django.urls import reverse

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.operations.presentation import short_ref
from apps.payments.models import Order, PaymentAttempt, PaymentPlan, TaraWebhookEvent
from apps.payments.services import OrderService, PaymentAttemptService, PaymentCreditService
from integrations.payments.tara.schemas import TaraPaymentStatusResponse, TaraTransactionStatusResponse

pytestmark = pytest.mark.django_db

LIST_URL = reverse("operations:order_list")


def make_order(email, first_name="Sarah", count=1, amount=Decimal("1000.00")):
    course = Course.objects.get_or_create(cf_course_id="crs_ventes", defaults={"name": "Cheveux crépus", "workspace_id": "ws"})[0]
    plan = PaymentPlan.objects.create(
        code=f"plan_{email}", name="Formule", course=course,
        installment_count=count, installment_amount=amount, is_active=True,
    )
    contact = Contact.objects.create(email=email, first_name=first_name, last_name="Nguimgo")
    order, _ = OrderService().create_order(contact, plan.id, f"idem-{email}")
    return order


def with_link(order, sequence=1):
    attempt = PaymentAttemptService().create_attempt(order.installments.get(sequence=sequence))
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentAttempt.objects.filter(pk=attempt.pk).update(general_link="https://taramoney.com/pay/x")
    attempt.refresh_from_db()
    return attempt


def reported_but_unconfirmed(order):
    attempt = with_link(order)
    TaraWebhookEvent.objects.create(
        dedup_key=f"d-{attempt.pk}", tara_product_id=attempt.tara_product_id, raw_provider_status="SUCCESS",
        processing_status=TaraWebhookEvent.ProcessingStatus.FAILED,
        failure_category=TaraWebhookEvent.FailureCategory.PROVIDER_LOOKUP_INDETERMINATE, payment_attempt=attempt,
    )
    return attempt


@pytest.fixture
def realistic_sales():
    """One sale per plain state the Ventes pages must render."""
    to_verify = make_order("to-verify@example.com")
    to_verify_attempt = reported_but_unconfirmed(to_verify)
    awaiting = make_order("awaiting@example.com", first_name="Awa")
    with_link(awaiting)
    paid = make_order("paid@example.com", first_name="Paul")
    PaymentCreditService().apply_verified_success(with_link(paid).id)
    multi = make_order("multi@example.com", first_name="Marie", count=3, amount=Decimal("20000.00"))
    PaymentCreditService().apply_verified_success(with_link(multi).id)
    expired = make_order("expired@example.com", first_name="Eric")
    Order.objects.filter(pk=expired.pk).update(status=Order.Status.EXPIRED)
    not_started = make_order("not-started@example.com", first_name="Noé")
    return {
        "to_verify": to_verify, "to_verify_attempt": to_verify_attempt, "awaiting": awaiting,
        "paid": paid, "multi": multi, "expired": expired, "not_started": not_started,
    }


# --- Ventes list ---

def test_list_shows_plain_states_and_no_uuid_column(ops_client, realistic_sales):
    response = ops_client.get(LIST_URL)
    content = response.content.decode()
    assert response.status_code == 200
    for label in ("À vérifier", "En attente de paiement", "Payée", "Paiement en plusieurs fois", "Paiement non démarré"):
        assert label in content
    assert short_ref(realistic_sales["paid"].reference) in content
    states = {o.pk: o.state.key for o in response.context["page_obj"]}
    assert states[realistic_sales["to_verify"].pk] == "to_verify"
    assert realistic_sales["expired"].pk not in states  # hidden by default


def test_a_verifier_filter_returns_the_reported_unconfirmed_sale(ops_client, realistic_sales):
    response = ops_client.get(LIST_URL, {"etat": "a_verifier"})
    results = list(response.context["page_obj"])
    assert realistic_sales["to_verify"] in results
    assert realistic_sales["awaiting"] not in results
    assert realistic_sales["paid"] not in results
    assert response.context["to_verify_count"] == 1


def test_etat_filters_map_to_statuses(ops_client, realistic_sales):
    payees = list(ops_client.get(LIST_URL, {"etat": "payees"}).context["page_obj"])
    assert payees == [realistic_sales["paid"]]
    expirees = list(ops_client.get(LIST_URL, {"etat": "expirees"}).context["page_obj"])
    assert expirees == [realistic_sales["expired"]]


def test_search_by_short_reference(ops_client, realistic_sales):
    order = realistic_sales["awaiting"]
    response = ops_client.get(LIST_URL, {"q": short_ref(order.reference)})
    assert list(response.context["page_obj"]) == [order]


# --- Vente page ---

def test_detail_shows_primary_verify_action_for_reported_sale(ops_client, realistic_sales):
    order = realistic_sales["to_verify"]
    response = ops_client.get(reverse("operations:order_detail", args=[order.reference]))
    content = response.content.decode()
    verify_url = reverse("operations:payment_attempt_check_status", args=[realistic_sales["to_verify_attempt"].pk])
    assert response.status_code == 200
    assert 'id="sale-primary-action"' in content
    assert verify_url in content
    assert "Vérifier le paiement" in content
    assert "Tara signale un paiement" in content


@pytest.mark.parametrize("key", ["awaiting", "paid", "multi", "expired", "not_started"])
def test_every_sale_state_renders(ops_client, realistic_sales, key):
    order = realistic_sales[key]
    response = ops_client.get(reverse("operations:order_detail", args=[order.reference]))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Détails techniques" in content
    assert 'id="sale-primary-action"' not in content  # nothing to verify on these


def test_multi_installment_detail_uses_words(ops_client, realistic_sales):
    response = ops_client.get(reverse("operations:order_detail", args=[realistic_sales["multi"].reference]))
    content = response.content.decode()
    assert "Versement 1 sur 3" in content
    assert "Payé le" in content
    assert "3 × 20 000 XAF, tous les" in content


# --- Confirmation page + success messages ---

def test_check_status_confirmation_page_is_plain(ops_client, realistic_sales):
    attempt = realistic_sales["to_verify_attempt"]
    response = ops_client.get(reverse("operations:payment_attempt_check_status", args=[attempt.pk]))
    content = response.content.decode()
    assert "Vérifier le paiement auprès de Tara" in content
    assert "Vérifier maintenant" in content
    assert short_ref(realistic_sales["to_verify"].reference) in content
    assert attempt.tara_product_id not in content


def _mock_tara(mocker, attempt, status):
    """No webhook paymentId here, so verification falls back to the productId lookup."""
    client = Mock()
    client.check_payment_status.return_value = TaraPaymentStatusResponse.model_validate({"status": "ERROR"})
    client.check_transaction_status.return_value = TaraTransactionStatusResponse.model_validate(
        {"productId": attempt.tara_product_id, "status": status, "message": "ok"},
    )
    mocker.patch("apps.payments.admin_services.TaraConfigService.get_client", return_value=client)


def test_check_status_success_message_wording(ops_client, mocker):
    order = make_order("check-success@example.com")
    attempt = with_link(order)
    _mock_tara(mocker, attempt, "SUCCESS")

    response = ops_client.post(
        reverse("operations:payment_attempt_check_status", args=[attempt.pk]),
        {"confirm_apply": "1", "reason": "client a payé"}, follow=True,
    )
    messages = [str(m) for m in response.context["messages"]]
    assert any("Paiement confirmé par Tara — la vente est payée" in m for m in messages)


def test_check_status_not_confirmed_message_wording(ops_client, mocker):
    order = make_order("check-pending@example.com")
    attempt = with_link(order)
    _mock_tara(mocker, attempt, "PENDING")

    response = ops_client.post(
        reverse("operations:payment_attempt_check_status", args=[attempt.pk]),
        {"confirm_apply": "1", "reason": "client a payé"}, follow=True,
    )
    messages = [str(m) for m in response.context["messages"]]
    assert any("Tara ne confirme pas encore ce paiement" in m for m in messages)


def test_every_action_confirmation_page_renders_plainly(ops_client, realistic_sales):
    from apps.provisioning.models import ProvisioningRequest

    paid = realistic_sales["paid"]
    awaiting = realistic_sales["awaiting"]
    open_installment = realistic_sales["multi"].installments.get(sequence=2)
    confirmation = paid.payment_confirmations.get()
    provisioning = ProvisioningRequest.objects.get(order=paid)
    urls = [
        reverse("operations:order_cancel", args=[awaiting.reference]),
        reverse("operations:order_disposition", args=[awaiting.reference]) + "?value=NEEDS_REVIEW",
        reverse("operations:order_freeze_enrollment", args=[paid.reference]),
        reverse("operations:order_resume_enrollment", args=[paid.reference]),
        reverse("operations:installment_cancel", args=[open_installment.pk]),
        reverse("operations:installment_waive", args=[open_installment.pk]),
        reverse("operations:payment_attempt_check_status", args=[realistic_sales["to_verify_attempt"].pk]),
        reverse("operations:confirmation_retry", args=[confirmation.pk]),
        reverse("operations:provisioning_retry", args=[provisioning.pk]),
    ]
    for url in urls:
        response = ops_client.get(url)
        content = response.content.decode()
        assert response.status_code == 200, url
        assert "Pourquoi ?" in content and "Revenir sans rien changer" in content, url
        assert "Vente #" in content, url
        assert "Commande " not in content, url
