import hashlib
import hmac
import logging

import pytest
import responses
from django.test import RequestFactory

from integrations.payments.tara.client import TaraClient
from integrations.payments.tara.exceptions import TaraInvalidRequestError, TaraMalformedResponseError


@pytest.fixture
def client():
    return TaraClient(api_key="test_api_key", webhook_secret="test_webhook_secret", business_id="test_business_id")


# --- Phase 9: POST /tara/paid/transactionlist (real documented contract) ---

@responses.activate
def test_list_paid_transactions_posts_documented_body(client):
    responses.add(
        responses.POST, f"{TaraClient.BASE_URL}/paid/transactionlist",
        json=[{"transactionId": "txn_1", "amount": 100, "currency": "XAF", "status": "PAID", "paidAt": "2026-01-01T00:00:00Z"}],
        status=200,
    )

    items = client.list_paid_transactions(start=0, size=20)

    assert len(items) == 1
    assert items[0].transaction_id == "txn_1"
    import json
    sent_body = json.loads(responses.calls[0].request.body)
    assert sent_body["apiKey"] == "test_api_key"
    assert sent_body["businessId"] == "test_business_id"
    assert sent_body["start"] == 0
    assert sent_body["size"] == 20


@responses.activate
def test_list_paid_transactions_stops_when_page_shorter_than_size(client):
    responses.add(
        responses.POST, f"{TaraClient.BASE_URL}/paid/transactionlist",
        json=[{"transactionId": "txn_1", "status": "PAID"}], status=200,
    )
    items = client.list_paid_transactions(start=0, size=20)
    assert len(items) == 1  # caller decides pagination is done; client itself makes no such assumption


def test_list_paid_transactions_rejects_oversized_page(client):
    with pytest.raises(TaraInvalidRequestError):
        client.list_paid_transactions(start=0, size=99999)


def test_list_paid_transactions_rejects_negative_start(client):
    with pytest.raises(TaraInvalidRequestError):
        client.list_paid_transactions(start=-1, size=10)


@responses.activate
def test_list_paid_transactions_rejects_non_array_response(client):
    responses.add(
        responses.POST, f"{TaraClient.BASE_URL}/paid/transactionlist",
        json={"transactions": []}, status=200,
    )
    with pytest.raises(TaraMalformedResponseError):
        client.list_paid_transactions(start=0, size=20)


@responses.activate
def test_list_paid_transactions_rejects_malformed_item(client):
    responses.add(
        responses.POST, f"{TaraClient.BASE_URL}/paid/transactionlist",
        json=["not an object"], status=200,
    )
    with pytest.raises(TaraMalformedResponseError):
        client.list_paid_transactions(start=0, size=20)


@responses.activate
def test_list_paid_transactions_no_secrets_in_logs(client, caplog):
    caplog.set_level(logging.INFO)
    responses.add(
        responses.POST, f"{TaraClient.BASE_URL}/paid/transactionlist",
        json=[{"transactionId": "txn_1", "status": "PAID"}], status=200,
    )
    client.list_paid_transactions(start=0, size=20)
    for record in caplog.records:
        assert "test_api_key" not in record.getMessage()


def test_verify_webhook_signature_valid(client):
    factory = RequestFactory()
    body = b'{"transaction_id": "txn_1"}'
    signature = hmac.new(b"test_webhook_secret", body, hashlib.sha256).hexdigest()
    request = factory.post("/api/tara/webhook/", data=body, content_type="application/json")
    request.META["HTTP_X_TARA_SIGNATURE"] = signature

    assert client.verify_webhook_signature(request) is True


def test_verify_webhook_signature_invalid(client):
    factory = RequestFactory()
    body = b'{"transaction_id": "txn_1"}'
    request = factory.post("/api/tara/webhook/", data=body, content_type="application/json")
    request.META["HTTP_X_TARA_SIGNATURE"] = "not-the-right-signature"

    assert client.verify_webhook_signature(request) is False


def test_verify_webhook_signature_missing_header(client):
    factory = RequestFactory()
    request = factory.post("/api/tara/webhook/", data=b"{}", content_type="application/json")

    assert client.verify_webhook_signature(request) is False
