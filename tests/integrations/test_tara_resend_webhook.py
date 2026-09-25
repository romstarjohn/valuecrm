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

URL = "https://www.dklo.co/api/tara/resend-webhook"


@pytest.fixture
def client():
    return TaraClient(api_key="secret-api-key-value", webhook_secret="secret-webhook-value", business_id="biz_123")


# --- Request shape ---

@responses.activate
def test_correct_endpoint_and_method(client):
    responses.add(responses.POST, URL, json={"status": "success"}, status=200)
    client.resend_webhook("attempt-1")
    assert len(responses.calls) == 1
    assert responses.calls[0].request.method == "POST"
    assert responses.calls[0].request.url.startswith(URL)


@responses.activate
def test_credentials_loaded_internally(client):
    responses.add(responses.POST, URL, json={"status": "success"}, status=200)
    client.resend_webhook("attempt-1")
    body = json.loads(responses.calls[0].request.body)
    assert body["apiKey"] == "secret-api-key-value"
    assert body["businessId"] == "biz_123"
    assert body["productId"] == "attempt-1"


def test_caller_cannot_override_business_id_or_api_key():
    params = set(inspect.signature(TaraClient.resend_webhook).parameters)
    assert "api_key" not in params
    assert "business_id" not in params


# --- Undocumented response shape: passed through as-is, never validated against a schema ---

@responses.activate
def test_response_returned_as_raw_parsed_json(client):
    responses.add(responses.POST, URL, json={"anything": "tara wants to send back"}, status=200)
    result = client.resend_webhook("attempt-1")
    assert result == {"anything": "tara wants to send back"}


@responses.activate
def test_empty_object_response_accepted(client):
    responses.add(responses.POST, URL, json={}, status=200)
    result = client.resend_webhook("attempt-1")
    assert result == {}


# --- Malformed responses ---

@responses.activate
def test_malformed_json_response(client):
    responses.add(responses.POST, URL, body="not json{{{", status=200, content_type="application/json")
    with pytest.raises(TaraMalformedResponseError):
        client.resend_webhook("attempt-1")


# --- Transport-level failures ---

@responses.activate
def test_timeout(client):
    responses.add(responses.POST, URL, body=requests.exceptions.Timeout())
    with pytest.raises(TaraTimeoutError):
        client.resend_webhook("attempt-1")


@responses.activate
def test_connection_failure(client):
    responses.add(responses.POST, URL, body=requests.exceptions.ConnectionError())
    with pytest.raises(TaraConnectionError):
        client.resend_webhook("attempt-1")


@responses.activate
def test_http_4xx(client):
    responses.add(responses.POST, URL, json={"message": "bad request"}, status=400)
    with pytest.raises(TaraClientError):
        client.resend_webhook("attempt-1")


@responses.activate
def test_http_5xx(client):
    responses.add(responses.POST, URL, json={"message": "unavailable"}, status=503)
    with pytest.raises(TaraServerError):
        client.resend_webhook("attempt-1")


# --- Security ---

@responses.activate
def test_api_key_absent_from_logs(client, caplog):
    responses.add(responses.POST, URL, json={"status": "success"}, status=200)
    caplog.set_level(logging.INFO)
    client.resend_webhook("attempt-1")
    for record in caplog.records:
        assert "secret-api-key-value" not in record.getMessage()
        assert "secret-webhook-value" not in record.getMessage()


def test_api_key_absent_from_exception_strings(client):
    with pytest.raises(Exception) as exc_info:  # noqa: BLE001 -- intentionally broad, just checking the message text
        client.resend_webhook("")
    assert "secret-api-key-value" not in str(exc_info.value)
