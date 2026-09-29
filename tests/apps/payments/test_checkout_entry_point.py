"""
Customer entry point (step 1 of the walkthrough, 2026-09-29): A timeout trap,
B clear "not started" page with one-click retry, C privacy notice, D short
reference, E stale-link replacement, F order link in the e-mail, plus the
confirmation e-mail / access opened right after verification.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock

import pytest
from django.core import mail
from django.test import Client
from django.utils import timezone

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import Order, PaymentAttempt, PaymentConfirmation, PaymentPlan
from apps.payments.services import CheckoutService, PaymentCreditService
from integrations.payments.tara.exceptions import TaraProviderBusinessError, TaraTimeoutError
from integrations.payments.tara.schemas import TaraPaymentLinkResponse

pytestmark = pytest.mark.django_db


@pytest.fixture
def plan():
    course = Course.objects.create(cf_course_id="crs_entry", name="Cheveux crépus", workspace_id="w")
    return PaymentPlan.objects.create(code="entry-1x", name="Paiement unique", course=course, installment_count=1,
                                      installment_amount=Decimal("1000.00"), is_active=True)


@pytest.fixture
def tara(mocker):
    client = Mock()
    client.create_payment_link.return_value = TaraPaymentLinkResponse.model_validate(
        {"status": "success", "message": "ok", "generalLink": "https://taramoney.com/pay/fresh"},
    )
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=client)
    return client


def submit(client, plan, key="k1", email="awa@example.com"):
    return client.post(f"/paiement/{plan.course.slug}/", {
        "plan_id": plan.id, "idempotency_key": key, "email": email,
        "phone": "+237 690 00 00 00", "first_name": "Awa", "last_name": "N.",
    })


# --- B: payment could not start ---

@pytest.mark.parametrize("error", [TaraProviderBusinessError("no"), TaraTimeoutError("slow")])
def test_failed_link_creation_shows_not_started_page_then_one_click_retry(tara, plan, error):
    tara.create_payment_link.side_effect = error
    browser = Client()

    page = submit(browser, plan)

    html = page.content.decode()
    assert page.status_code == 200
    assert "Le paiement n’a pas pu démarrer" in html and "Rien n’a été débité" in html
    assert "En attente de paiement" not in html and "Vérification en cours" not in html
    order = Order.objects.get()
    assert f"#{str(order.reference)[:8].upper()}" in html  # D

    tara.create_payment_link.side_effect = None
    token = CheckoutService().build_signed_reference(order.reference)
    retry = browser.post(f"/paiement/status/{token}/retry/")

    assert retry.status_code == 302 and retry["Location"] == "https://taramoney.com/pay/fresh"
    assert Order.objects.count() == 1  # same sale, nothing to retype


def test_status_page_for_never_started_payment_is_not_misleading(tara, plan):
    tara.create_payment_link.side_effect = TaraTimeoutError("slow")
    submit(Client(), plan)
    order = Order.objects.get()

    html = Client().get(f"/paiement/status/{CheckoutService().build_signed_reference(order.reference)}/").content.decode()

    assert "Le paiement n’a pas pu démarrer" in html and "Réessayer le paiement" in html


# --- A: timeout trap ---

def test_returning_after_timeout_gets_a_payment_link(tara, plan):
    tara.create_payment_link.side_effect = TaraTimeoutError("slow")
    tara.check_transaction_status.side_effect = TaraTimeoutError("still slow")
    submit(Client(), plan, key="visit-1")

    tara.create_payment_link.side_effect = None
    response = submit(Client(), plan, key="visit-2")  # customer reopens the offer page

    assert response["Location"] == "https://taramoney.com/pay/fresh"
    assert Order.objects.count() == 1


# --- E: stale stored link ---

def test_old_stored_link_is_replaced_fresh_one_reused(tara, plan, settings):
    settings.CHECKOUT_LINK_REUSE_MAX_AGE_MINUTES = 60
    submit(Client(), plan, key="v1")
    assert tara.create_payment_link.call_count == 1

    submit(Client(), plan, key="v2")  # 1 minute later: reused
    assert tara.create_payment_link.call_count == 1

    PaymentAttempt.objects.update(initiated_at=timezone.now() - timedelta(hours=2))
    submit(Client(), plan, key="v3")  # 2 hours later: fresh link
    assert tara.create_payment_link.call_count == 2
    assert PaymentAttempt.objects.filter(status=PaymentAttempt.Status.EXPIRED).count() == 1


def test_link_with_payment_under_way_is_never_replaced(tara, plan):
    submit(Client(), plan, key="v1")
    PaymentAttempt.objects.update(status=PaymentAttempt.Status.PENDING, initiated_at=timezone.now() - timedelta(days=1))

    submit(Client(), plan, key="v2")

    assert tara.create_payment_link.call_count == 1


# --- C: privacy notice ---

def test_privacy_notice_with_optional_policy_link(plan, settings):
    html = Client().get(f"/paiement/{plan.course.slug}/").content.decode()
    assert "servent uniquement à traiter votre commande" in html and "Politique de confidentialité" not in html

    settings.PRIVACY_POLICY_URL = "https://example.com/confidentialite"
    html = Client().get(f"/paiement/{plan.course.slug}/").content.decode()
    assert 'href="https://example.com/confidentialite"' in html


# --- F + immediate follow-ups ---

def _verified_payment(plan, tara):
    submit(Client(), plan)
    return PaymentAttempt.objects.get()


def test_confirmation_email_sent_right_after_verification(tara, plan, settings, django_capture_on_commit_callbacks, mocker):
    settings.PAYMENT_FOLLOWUPS_MODE = "inline"
    execute = mocker.patch("apps.provisioning.services.ProvisioningService.execute")
    attempt = _verified_payment(plan, tara)

    with django_capture_on_commit_callbacks(execute=True):
        PaymentCreditService().apply_verified_success(attempt.id)

    assert len(mail.outbox) == 1  # no scheduled job needed
    message = mail.outbox[0]
    assert "Paiement confirmé" in message.subject
    assert "confirmé sa bonne réception" in message.body and "/paiement/status/" in message.body  # F
    assert PaymentConfirmation.objects.get().status == PaymentConfirmation.Status.SENT
    execute.assert_called_once()  # course access opened right away (FIRST... / full payment eligible)


def test_background_mode_uses_a_thread(tara, plan, settings, django_capture_on_commit_callbacks, mocker):
    settings.PAYMENT_FOLLOWUPS_MODE = "thread"
    thread = mocker.patch("threading.Thread")
    attempt = _verified_payment(plan, tara)

    with django_capture_on_commit_callbacks(execute=True):
        PaymentCreditService().apply_verified_success(attempt.id)

    thread.assert_called_once()
    thread.return_value.start.assert_called_once()


def test_status_link_in_email_stays_valid_for_weeks(plan, tara):
    submit(Client(), plan)
    order = Order.objects.get()
    token = CheckoutService().build_signed_reference(order.reference)

    from django.core import signing
    import time
    real_time = time.time
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(signing.time, "time", lambda: real_time() + 20 * 24 * 3600)
        assert Client().get(f"/paiement/status/{token}/").status_code == 200
