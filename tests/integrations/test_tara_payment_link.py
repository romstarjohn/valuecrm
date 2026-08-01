import inspect
import logging
from decimal import Decimal
from unittest.mock import patch

import pytest
import requests
import responses

from integrations.payments.tara.client import TaraClient
from shared.constants import TARA_CONNECT_TIMEOUT_SECONDS, TARA_READ_TIMEOUT_SECONDS
from integrations.payments.tara.exceptions import (
    TaraClientError,
    TaraConnectionError,
    TaraInvalidRequestError,
    TaraMalformedResponseError,
    TaraProviderBusinessError,
    TaraServerError,
    TaraTimeoutError,
)

URL = "https://www.dklo.co/api/paymentlinks"


@pytest.fixture
def client():
    return TaraClient(api_key="secret-api-key-value", webhook_secret="secret-webhook-value", business_id="biz_123")


def call(client, **overrides):
    kwargs = dict(
        product_id="attempt-abc123",
        product_name="Bootcamp — 3 installments",
        product_price=Decimal("34000"),
        product_description="Installment 1 of 3",
        web_hook_url="https://example.com/webhooks/tara",
    )
    kwargs.update(overrides)
    return client.create_payment_link(**kwargs)


# --- Request shape / endpoint ---

@responses.activate
def test_correct_endpoint_and_method(client):
    responses.add(responses.POST, URL, json={"status": "success", "message": "ok"}, status=200)
    call(client)
    assert len(responses.calls) == 1
    assert responses.calls[0].request.method == "POST"
    assert responses.calls[0].request.url.startswith(URL)


@responses.activate
def test_correct_json_shape_and_credentials_loaded_internally(client):
    responses.add(responses.POST, URL, json={"status": "success", "message": "ok"}, status=200)
    call(client, product_picture_url="https://example.com/pic.png", return_url="https://example.com/return")

    import json
    body = json.loads(responses.calls[0].request.body)
    assert body["apiKey"] == "secret-api-key-value"
    assert body["businessId"] == "biz_123"
    assert body["productId"] == "attempt-abc123"
    assert body["productName"] == "Bootcamp — 3 installments"
    assert body["productPrice"] == 34000
    assert isinstance(body["productPrice"], int)
    assert body["productDescription"] == "Installment 1 of 3"
    assert body["webHookUrl"] == "https://example.com/webhooks/tara"
    assert body["productPictureUrl"] == "https://example.com/pic.png"
    assert body["returnUrl"] == "https://example.com/return"


def test_caller_cannot_override_business_id_api_key_or_base_url():
    """create_payment_link's signature has no apiKey/businessId/base_url parameter at all — not just validated away, structurally absent."""
    params = set(inspect.signature(TaraClient.create_payment_link).parameters)
    assert "api_key" not in params
    assert "business_id" not in params
    assert "base_url" not in params
    assert not hasattr(TaraClient, "BASE_URL") or TaraClient.BASE_URL == "https://www.dklo.co/api"


@responses.activate
def test_optional_fields_omitted_when_not_supplied(client):
    responses.add(responses.POST, URL, json={"status": "success", "message": "ok"}, status=200)
    call(client)  # no product_picture_url/return_url

    import json
    body = json.loads(responses.calls[0].request.body)
    assert "productPictureUrl" not in body
    assert "returnUrl" not in body


# --- Amount validation ---

@responses.activate
def test_positive_integer_amount_accepted(client):
    responses.add(responses.POST, URL, json={"status": "success", "message": "ok"}, status=200)
    call(client, product_price=Decimal("100000"))  # should not raise


def test_fractional_amount_rejected(client):
    with pytest.raises(TaraInvalidRequestError):
        call(client, product_price=Decimal("34000.50"))


def test_zero_amount_rejected(client):
    with pytest.raises(TaraInvalidRequestError):
        call(client, product_price=Decimal("0"))


def test_negative_amount_rejected(client):
    with pytest.raises(TaraInvalidRequestError):
        call(client, product_price=Decimal("-100"))


# --- URL validation ---

def test_non_https_webhook_url_rejected(client):
    with pytest.raises(TaraInvalidRequestError):
        call(client, web_hook_url="http://example.com/webhooks/tara")


def test_invalid_return_url_rejected(client):
    with pytest.raises(TaraInvalidRequestError):
        call(client, return_url="not-a-url")


def test_non_https_return_url_rejected(client):
    with pytest.raises(TaraInvalidRequestError):
        call(client, return_url="http://example.com/return")


# --- Transport-level failures ---

@responses.activate
def test_no_automatic_retry_on_server_error(client):
    responses.add(responses.POST, URL, json={"message": "boom"}, status=500)
    with pytest.raises(TaraServerError):
        call(client)
    assert len(responses.calls) == 1  # exactly one attempt — no retry


@responses.activate
def test_timeout_classified_as_unknown_not_definitive_failure(client):
    responses.add(responses.POST, URL, body=requests.exceptions.Timeout())
    with pytest.raises(TaraTimeoutError):
        call(client)


@responses.activate
def test_connection_failure(client):
    responses.add(responses.POST, URL, body=requests.exceptions.ConnectionError())
    with pytest.raises(TaraConnectionError):
        call(client)


@responses.activate
def test_http_4xx_raises_client_error(client):
    responses.add(responses.POST, URL, json={"message": "bad request"}, status=400)
    with pytest.raises(TaraClientError):
        call(client)


@responses.activate
def test_http_5xx_raises_server_error(client):
    responses.add(responses.POST, URL, json={"message": "unavailable"}, status=503)
    with pytest.raises(TaraServerError):
        call(client)


# --- Response validation ---

@responses.activate
def test_complete_successful_response(client):
    responses.add(responses.POST, URL, json={
        "status": "success", "message": "ok",
        "whatsappLink": "https://wa.me/123", "telegramLink": "https://t.me/123",
        "dikaloLink": "https://dikalo.example/123", "generalLink": "https://pay.example/123",
        "cardLink": "https://card.example/123", "smsLink": "sms:+123?body=pay",
    }, status=200)
    result = call(client)
    assert result.is_success
    assert result.whatsapp_link == "https://wa.me/123"
    assert result.sms_link == "sms:+123?body=pay"


@responses.activate
def test_valid_partial_optional_link_response(client):
    responses.add(responses.POST, URL, json={"status": "success", "message": "ok", "generalLink": "https://pay.example/123"}, status=200)
    result = call(client)
    assert result.is_success
    assert result.general_link == "https://pay.example/123"
    assert result.whatsapp_link is None
    assert result.sms_link is None


@responses.activate
def test_provider_business_failure_with_http_200(client):
    responses.add(responses.POST, URL, json={"status": "failure", "message": "insufficient data"}, status=200)
    with pytest.raises(TaraProviderBusinessError):
        call(client)


@responses.activate
def test_unknown_status_rejected_safely(client):
    """Only the literal "success" is treated as success — any other status, even an unrecognized one, is a business failure, never silently accepted."""
    responses.add(responses.POST, URL, json={"status": "weird_new_status", "message": "?"}, status=200)
    with pytest.raises(TaraProviderBusinessError):
        call(client)


@responses.activate
def test_malformed_json_response(client):
    responses.add(responses.POST, URL, body="not json{{{", status=200, content_type="application/json")
    with pytest.raises(TaraMalformedResponseError):
        call(client)


@responses.activate
def test_array_response_rejected(client):
    responses.add(responses.POST, URL, json=[{"status": "success"}], status=200)
    with pytest.raises(TaraMalformedResponseError):
        call(client)


@responses.activate
def test_string_response_rejected(client):
    responses.add(responses.POST, URL, json="success", status=200)
    with pytest.raises(TaraMalformedResponseError):
        call(client)


@responses.activate
def test_null_response_rejected(client):
    responses.add(responses.POST, URL, json=None, status=200)
    with pytest.raises(TaraMalformedResponseError):
        call(client)


@responses.activate
def test_missing_required_status_field_rejected(client):
    responses.add(responses.POST, URL, json={"message": "ok"}, status=200)
    with pytest.raises(TaraMalformedResponseError):
        call(client)


@responses.activate
def test_invalid_http_link_in_response_rejected(client):
    responses.add(responses.POST, URL, json={"status": "success", "generalLink": "not-a-url"}, status=200)
    with pytest.raises(TaraMalformedResponseError):
        call(client)


@responses.activate
def test_sms_uri_accepted_not_treated_as_http_url(client):
    responses.add(responses.POST, URL, json={"status": "success", "smsLink": "sms:+237600000000?body=hello"}, status=200)
    result = call(client)
    assert result.sms_link == "sms:+237600000000?body=hello"


# --- Security ---

@responses.activate
def test_api_key_absent_from_logs(client, caplog):
    responses.add(responses.POST, URL, json={"status": "success", "message": "ok"}, status=200)
    caplog.set_level(logging.INFO)
    call(client)
    for record in caplog.records:
        message = record.getMessage()
        assert "secret-api-key-value" not in message
        assert "secret-webhook-value" not in message


@responses.activate
def test_sensitive_response_links_absent_from_logs(client, caplog):
    responses.add(responses.POST, URL, json={
        "status": "success", "generalLink": "https://pay.example/token-abc123secret",
        "cardLink": "https://card.example/token-xyz789secret",
    }, status=200)
    caplog.set_level(logging.INFO)
    call(client)
    for record in caplog.records:
        message = record.getMessage()
        assert "token-abc123secret" not in message
        assert "token-xyz789secret" not in message


def test_api_key_absent_from_exception_strings(client):
    with pytest.raises(TaraInvalidRequestError) as exc_info:
        call(client, product_price=Decimal("-1"))
    assert "secret-api-key-value" not in str(exc_info.value)
    assert "secret-webhook-value" not in str(exc_info.value)


@responses.activate
def test_api_key_absent_from_transport_error_strings(client):
    responses.add(responses.POST, URL, json={"message": "bad request"}, status=400)
    with pytest.raises(TaraClientError) as exc_info:
        call(client)
    assert "secret-api-key-value" not in str(exc_info.value)


def test_tls_verification_is_not_disabled(client):
    assert client.session.verify is not False


def test_explicit_connect_and_read_timeouts_and_no_redirects(client):
    """
    responses() intercepts at the wire level and can't observe python-level
    kwargs like timeout/allow_redirects, so this uses a direct mock of
    session.request to verify what's actually passed to the transport layer.
    """
    with patch.object(client.session, "request") as mock_request:
        mock_request.return_value.content = b'{"status": "success", "message": "ok"}'
        mock_request.return_value.status_code = 200
        mock_request.return_value.json.return_value = {"status": "success", "message": "ok"}
        call(client)

    _, kwargs = mock_request.call_args
    assert kwargs["timeout"] == (TARA_CONNECT_TIMEOUT_SECONDS, TARA_READ_TIMEOUT_SECONDS)
    assert kwargs["allow_redirects"] is False


@responses.activate
def test_decrypted_credentials_absent_from_response_object(client):
    responses.add(responses.POST, URL, json={"status": "success", "message": "ok"}, status=200)
    result = call(client)
    assert not hasattr(result, "api_key")
    assert not hasattr(result, "apiKey")
    assert "secret-api-key-value" not in result.model_dump_json()


# --- Phase 5 correction 1: a reflected-secret provider response must never leak ---

@responses.activate
def test_reflected_api_key_in_4xx_body_does_not_leak_into_exception_or_logs(client, caplog):
    """A buggy/hostile Tara response could echo the request's apiKey back in an error body — this must never appear anywhere, not even truncated."""
    caplog.set_level(logging.INFO)
    responses.add(responses.POST, URL, json={"message": "Invalid apiKey: secret-api-key-value provided"}, status=400)

    with pytest.raises(TaraClientError) as exc_info:
        call(client)

    assert "secret-api-key-value" not in str(exc_info.value)
    assert not hasattr(exc_info.value, "response_excerpt")
    for record in caplog.records:
        assert "secret-api-key-value" not in record.getMessage()


@responses.activate
def test_reflected_phone_number_in_5xx_body_does_not_leak(client, caplog):
    caplog.set_level(logging.INFO)
    responses.add(responses.POST, URL, json={"message": "failed for phone +237600000000"}, status=500)

    with pytest.raises(TaraServerError) as exc_info:
        call(client)

    assert "+237600000000" not in str(exc_info.value)
    for record in caplog.records:
        assert "+237600000000" not in record.getMessage()


@responses.activate
def test_reflected_payment_url_in_business_failure_body_does_not_leak(client, caplog):
    """HTTP 200 but status != "success" — the business-failure path. Tara's "message"/"status" fields could echo a payment URL; it must not appear anywhere."""
    caplog.set_level(logging.INFO)
    responses.add(responses.POST, URL, json={
        "status": "failure",
        "message": "duplicate of https://pay.example/token-super-secret-abc123",
    }, status=200)

    with pytest.raises(TaraProviderBusinessError) as exc_info:
        call(client)

    assert "token-super-secret-abc123" not in str(exc_info.value)
    assert not hasattr(exc_info.value, "raw_status")
    for record in caplog.records:
        assert "token-super-secret-abc123" not in record.getMessage()


@responses.activate
def test_reflected_secrets_in_status_field_itself_do_not_leak(client, caplog):
    """Even if the reflected secret is IN the "status" field (not "message"), TaraProviderBusinessError must not embed it — status is only str-typed, not a verified closed enum."""
    caplog.set_level(logging.INFO)
    responses.add(responses.POST, URL, json={
        "status": "failure: apiKey=secret-api-key-value phone=+237600000000",
    }, status=200)

    with pytest.raises(TaraProviderBusinessError) as exc_info:
        call(client)

    assert "secret-api-key-value" not in str(exc_info.value)
    assert "+237600000000" not in str(exc_info.value)
    for record in caplog.records:
        assert "secret-api-key-value" not in record.getMessage()
        assert "+237600000000" not in record.getMessage()


def test_api_error_exceptions_no_longer_carry_response_excerpt():
    """Structural guard: the field itself must not exist, not just be empty — prevents accidental reintroduction."""
    error = TaraClientError("test", status_code=400, operation="create_payment_link")
    assert not hasattr(error, "response_excerpt")
    assert error.operation == "create_payment_link"
