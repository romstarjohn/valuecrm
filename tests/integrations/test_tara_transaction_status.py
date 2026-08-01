import inspect
import json
import logging

import pytest
import requests
import responses

from integrations.payments.tara.client import TaraClient
from integrations.payments.tara.exceptions import (
    TaraClientError,
    TaraConnectionError,
    TaraMalformedResponseError,
    TaraServerError,
    TaraTimeoutError,
)
from integrations.payments.tara.schemas import TaraTransactionStatus

URL = "https://www.dklo.co/api/transactions/status"


@pytest.fixture
def client():
    return TaraClient(api_key="secret-api-key-value", webhook_secret="secret-webhook-value", business_id="biz_123")


# --- Request shape ---

@responses.activate
def test_correct_endpoint_and_method(client):
    responses.add(responses.POST, URL, json={"productId": "attempt-1", "status": "SUCCESS", "message": "ok"}, status=200)
    client.check_transaction_status("attempt-1")
    assert len(responses.calls) == 1
    assert responses.calls[0].request.method == "POST"
    assert responses.calls[0].request.url.startswith(URL)


@responses.activate
def test_credentials_loaded_internally(client):
    responses.add(responses.POST, URL, json={"productId": "attempt-1", "status": "SUCCESS", "message": "ok"}, status=200)
    client.check_transaction_status("attempt-1")
    body = json.loads(responses.calls[0].request.body)
    assert body["apiKey"] == "secret-api-key-value"
    assert body["businessId"] == "biz_123"
    assert body["productId"] == "attempt-1"


def test_caller_cannot_override_business_id_or_api_key():
    params = set(inspect.signature(TaraClient.check_transaction_status).parameters)
    assert "api_key" not in params
    assert "business_id" not in params


# --- Recognized statuses ---

@responses.activate
def test_success_status(client):
    responses.add(responses.POST, URL, json={"productId": "attempt-1", "status": "SUCCESS", "message": "paid"}, status=200)
    result = client.check_transaction_status("attempt-1")
    assert result.normalized_status == TaraTransactionStatus.SUCCESS


@responses.activate
def test_failure_status(client):
    responses.add(responses.POST, URL, json={"productId": "attempt-1", "status": "FAILURE", "message": "declined"}, status=200)
    result = client.check_transaction_status("attempt-1")
    assert result.normalized_status == TaraTransactionStatus.FAILURE


@responses.activate
def test_pending_status(client):
    responses.add(responses.POST, URL, json={"productId": "attempt-1", "status": "PENDING", "message": "waiting"}, status=200)
    result = client.check_transaction_status("attempt-1")
    assert result.normalized_status == TaraTransactionStatus.PENDING


@responses.activate
def test_unknown_status_mapped_safely_to_unknown(client):
    responses.add(responses.POST, URL, json={"productId": "attempt-1", "status": "SOME_NEW_STATUS", "message": "?"}, status=200)
    result = client.check_transaction_status("attempt-1")
    assert result.normalized_status == TaraTransactionStatus.UNKNOWN
    assert result.status == "SOME_NEW_STATUS"  # raw value preserved for diagnostics


@responses.activate
def test_lowercase_status_still_normalizes_correctly(client):
    responses.add(responses.POST, URL, json={"productId": "attempt-1", "status": "success", "message": "ok"}, status=200)
    result = client.check_transaction_status("attempt-1")
    assert result.normalized_status == TaraTransactionStatus.SUCCESS


# --- productId mismatch ---

@responses.activate
def test_returned_product_id_mismatch_is_rejected(client):
    responses.add(responses.POST, URL, json={"productId": "some-other-attempt", "status": "SUCCESS", "message": "ok"}, status=200)
    with pytest.raises(TaraMalformedResponseError):
        client.check_transaction_status("attempt-1")


# --- Malformed responses ---

@responses.activate
def test_malformed_json_response(client):
    responses.add(responses.POST, URL, body="not json{{{", status=200, content_type="application/json")
    with pytest.raises(TaraMalformedResponseError):
        client.check_transaction_status("attempt-1")


@responses.activate
def test_missing_required_fields_rejected(client):
    responses.add(responses.POST, URL, json={"message": "ok"}, status=200)  # missing productId and status
    with pytest.raises(TaraMalformedResponseError):
        client.check_transaction_status("attempt-1")


@responses.activate
def test_array_response_rejected(client):
    responses.add(responses.POST, URL, json=[{"productId": "attempt-1"}], status=200)
    with pytest.raises(TaraMalformedResponseError):
        client.check_transaction_status("attempt-1")


# --- Transport-level failures ---

@responses.activate
def test_timeout(client):
    responses.add(responses.POST, URL, body=requests.exceptions.Timeout())
    with pytest.raises(TaraTimeoutError):
        client.check_transaction_status("attempt-1")


@responses.activate
def test_connection_failure(client):
    responses.add(responses.POST, URL, body=requests.exceptions.ConnectionError())
    with pytest.raises(TaraConnectionError):
        client.check_transaction_status("attempt-1")


@responses.activate
def test_http_4xx(client):
    responses.add(responses.POST, URL, json={"message": "bad request"}, status=400)
    with pytest.raises(TaraClientError):
        client.check_transaction_status("attempt-1")


@responses.activate
def test_http_5xx(client):
    responses.add(responses.POST, URL, json={"message": "unavailable"}, status=503)
    with pytest.raises(TaraServerError):
        client.check_transaction_status("attempt-1")


# --- Security ---

@responses.activate
def test_api_key_absent_from_logs(client, caplog):
    responses.add(responses.POST, URL, json={"productId": "attempt-1", "status": "SUCCESS", "message": "ok"}, status=200)
    caplog.set_level(logging.INFO)
    client.check_transaction_status("attempt-1")
    for record in caplog.records:
        assert "secret-api-key-value" not in record.getMessage()
        assert "secret-webhook-value" not in record.getMessage()


def test_api_key_absent_from_exception_strings(client):
    with pytest.raises(Exception) as exc_info:  # noqa: BLE001 -- intentionally broad, just checking the message text
        client.check_transaction_status("")
    assert "secret-api-key-value" not in str(exc_info.value)
