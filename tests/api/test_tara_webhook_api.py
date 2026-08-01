"""
Phase 6 replaces this entire file. The previous 6 tests encoded a false
belief that integrations/payments/tara/signature.py's HMAC scheme was a real,
authoritative signature — Tara's documentation defines no header, algorithm,
signed byte sequence, encoding, timestamp, or replay window for it (see that
module's docstring). These tests instead prove the corrected, documented-safe
behavior: every webhook is untrusted input, used only as a trigger for
server-to-server verification via TaraClient.check_transaction_status().
"""
import json
import logging
from decimal import Decimal
from unittest.mock import Mock

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import Installment, PaymentAttempt, PaymentPlan, TaraConfig, TaraWebhookEvent
from apps.payments.services import OrderService, PaymentAttemptService
from integrations.payments.tara.schemas import TaraTransactionStatusResponse
from shared.security import encrypt_value

pytestmark = pytest.mark.django_db

WEBHOOK_URL = "/api/tara/webhook/"


@pytest.fixture
def active_config():
    return TaraConfig.objects.create(
        name="Main", business_id="biz_123", is_active=True,
        api_key=encrypt_value("key"), webhook_secret=encrypt_value("secret"),
    )


@pytest.fixture
def attempt():
    course = Course.objects.create(cf_course_id="crs_1", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code="bootcamp-1x", name="Bootcamp", course=course,
        installment_count=1, installment_amount=Decimal("100000.00"), is_active=True,
    )
    contact = Contact.objects.create(email="buyer@example.com")
    order, _ = OrderService().create_order(contact, plan.id, "idem-webhook-api-tests")
    installment = order.installments.get()
    a = PaymentAttemptService().create_attempt(installment)
    PaymentAttemptService().transition(a, PaymentAttempt.Status.LINK_CREATED)
    return a


def post_json(client, data: dict, content_type="application/json"):
    return client.post(WEBHOOK_URL, data=json.dumps(data), content_type=content_type)


# --- Routing and request handling ---

def test_webhook_route_is_mounted(client, active_config):
    response = post_json(client, {"businessId": "biz_123", "productId": "x", "status": "SUCCESS"})
    assert response.status_code in (200, 400)  # resolves to our view, not a 404


def test_post_is_accepted(client, active_config):
    response = post_json(client, {"businessId": "biz_123", "productId": "x", "status": "SUCCESS"})
    assert response.status_code == 200


def test_get_is_rejected(client):
    response = client.get(WEBHOOK_URL)
    assert response.status_code == 405


def test_non_json_content_type_rejected(client):
    response = client.post(WEBHOOK_URL, data="businessId=biz_123", content_type="application/x-www-form-urlencoded")
    assert response.status_code == 400


def test_malformed_json_rejected(client):
    response = client.post(WEBHOOK_URL, data="not json{{{", content_type="application/json")
    assert response.status_code == 400


def test_oversized_body_rejected(client):
    huge_payload = json.dumps({"businessId": "biz_123", "productId": "x", "status": "SUCCESS", "padding": "a" * 100_000})
    response = client.post(WEBHOOK_URL, data=huge_payload, content_type="application/json")
    assert response.status_code == 400


def test_array_body_rejected(client):
    response = client.post(WEBHOOK_URL, data=json.dumps([1, 2, 3]), content_type="application/json")
    assert response.status_code == 400


def test_primitive_body_rejected(client):
    response = client.post(WEBHOOK_URL, data=json.dumps("just a string"), content_type="application/json")
    assert response.status_code == 400


def test_null_body_rejected(client):
    response = client.post(WEBHOOK_URL, data=json.dumps(None), content_type="application/json")
    assert response.status_code == 400


def test_csrf_exemption_limited_to_webhook_route(client, active_config):
    """A request with NO CSRF token must still succeed on the webhook route (Tara can't supply Django's CSRF token) — but Django's global CsrfViewMiddleware remains active for every other route (verified in existing portal-view tests, e.g. login-required redirects still function)."""
    csrf_client = Client(enforce_csrf_checks=True)
    response = post_json(csrf_client, {"businessId": "biz_123", "productId": "x", "status": "SUCCESS"})
    assert response.status_code == 200  # not blocked by CSRF despite enforce_csrf_checks=True


def test_generic_response_does_not_reveal_business_id_validity(client, active_config):
    matching = post_json(client, {"businessId": "biz_123", "productId": "x1", "status": "SUCCESS"})
    mismatched = post_json(client, {"businessId": "wrong-business", "productId": "x2", "status": "SUCCESS"})
    assert matching.status_code == mismatched.status_code == 200
    assert matching.json() == mismatched.json()


def test_generic_response_does_not_reveal_product_id_existence(client, active_config, attempt, mocker):
    mock_client = Mock()
    mock_client.check_transaction_status.return_value = TaraTransactionStatusResponse.model_validate({
        "productId": attempt.tara_product_id, "status": "PENDING", "message": "waiting",
    })
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    known = post_json(client, {"businessId": "biz_123", "productId": attempt.tara_product_id, "status": "PENDING"})
    unknown = post_json(client, {"businessId": "biz_123", "productId": "totally-unknown-id", "status": "PENDING"})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()


@pytest.mark.django_db
def test_rate_limit_enforced(client, active_config, settings):
    cache.clear()
    settings.RATELIMIT_ENABLE = True
    try:
        statuses = [
            post_json(client, {"businessId": "biz_123", "productId": f"x{i}", "status": "SUCCESS"}).status_code
            for i in range(70)
        ]
        assert 403 in statuses
    finally:
        cache.clear()


# --- Trust ---

def test_webhook_success_alone_never_marks_payment_paid(client, attempt):
    """No active TaraConfig registered in this test — even a claimed SUCCESS must not credit anything."""
    post_json(client, {"businessId": "biz_123", "productId": attempt.tara_product_id, "status": "SUCCESS"})
    attempt.refresh_from_db()
    installment = Installment.objects.get(pk=attempt.installment_id)
    assert attempt.status != PaymentAttempt.Status.SUCCEEDED
    assert installment.status != Installment.Status.PAID


def test_unsupported_signature_header_never_authorizes_payment(client, active_config, attempt, mocker):
    """Sending a signature header (even a correctly-computed HMAC one) has zero effect — it is not checked at all."""
    mock_client = Mock()
    mock_client.check_transaction_status.return_value = TaraTransactionStatusResponse.model_validate({
        "productId": attempt.tara_product_id, "status": "FAILURE", "message": "declined",
    })
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    import hashlib
    import hmac as hmac_module
    payload = json.dumps({"businessId": "biz_123", "productId": attempt.tara_product_id, "status": "SUCCESS"}).encode()
    fake_signature = hmac_module.new(b"secret", payload, hashlib.sha256).hexdigest()
    response = client.post(WEBHOOK_URL, data=payload, content_type="application/json", HTTP_X_TARA_SIGNATURE=fake_signature)

    assert response.status_code == 200
    attempt.refresh_from_db()
    # Credited only because server-to-server status said FAILURE was actually checked — not because of the signature header.
    assert attempt.status == PaymentAttempt.Status.FAILED


def test_matching_business_id_required_for_correlation(client, active_config, attempt, mocker):
    mock_client = Mock()
    mock_client.check_transaction_status.return_value = TaraTransactionStatusResponse.model_validate({
        "productId": attempt.tara_product_id, "status": "SUCCESS", "message": "ok",
    })
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    post_json(client, {"businessId": "wrong-business", "productId": attempt.tara_product_id, "status": "SUCCESS"})

    attempt.refresh_from_db()
    assert attempt.status != PaymentAttempt.Status.SUCCEEDED
    mock_client.check_transaction_status.assert_not_called()


def test_unknown_business_id_is_quarantined_not_crashed(client):
    response = post_json(client, {"businessId": "never-configured", "productId": "x", "status": "SUCCESS"})
    assert response.status_code == 200
    event = TaraWebhookEvent.objects.get()
    assert event.processing_status == TaraWebhookEvent.ProcessingStatus.REJECTED
    assert event.failure_category == TaraWebhookEvent.FailureCategory.BUSINESS_ID_MISMATCH


def test_unknown_product_id_causes_no_tara_status_call(client, active_config, mocker):
    mock_client = Mock()
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    post_json(client, {"businessId": "biz_123", "productId": "never-seen-before", "status": "SUCCESS"})

    mock_client.check_transaction_status.assert_not_called()


def test_phone_amount_collection_id_cannot_match_an_order(client, active_config, attempt, mocker):
    mock_client = Mock()
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    post_json(client, {
        "businessId": "biz_123", "paymentId": "unrelated-id",
        "phoneNumber": "696717597", "amount": str(attempt.expected_amount), "collectionId": "27731",
        "status": "SUCCESS",
    })

    attempt.refresh_from_db()
    assert attempt.status != PaymentAttempt.Status.SUCCEEDED
    mock_client.check_transaction_status.assert_not_called()


def test_exact_product_id_invokes_status_verification(client, active_config, attempt, mocker):
    mock_client = Mock()
    mock_client.check_transaction_status.return_value = TaraTransactionStatusResponse.model_validate({
        "productId": attempt.tara_product_id, "status": "SUCCESS", "message": "ok",
    })
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    post_json(client, {"businessId": "biz_123", "productId": attempt.tara_product_id, "status": "SUCCESS"})

    mock_client.check_transaction_status.assert_called_once_with(attempt.tara_product_id)


def test_credentials_absent_from_response_body(client, active_config):
    response = post_json(client, {"businessId": "biz_123", "productId": "x", "status": "SUCCESS"})
    content = response.content.decode().lower()
    assert "apikey" not in content
    assert "secret" not in content


def test_credentials_absent_from_logs(client, active_config, caplog):
    caplog.set_level(logging.INFO)
    post_json(client, {"businessId": "biz_123", "productId": "x", "status": "SUCCESS"})
    for record in caplog.records:
        message = record.getMessage().lower()
        assert "apikey" not in message
        assert "secret" not in message
