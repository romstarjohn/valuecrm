import json
from decimal import Decimal

from integrations.payments.tara.schemas import TaraPaymentStatusResponse, TaraTransactionStatus


def test_parses_production_success_shape():
    """Shape of a real /transactions/status answer queried by paymentId (2026-09-25), values replaced."""
    response = TaraPaymentStatusResponse.model_validate({
        "status": "SUCCESS", "message": "API_ORDER_SUCESSFULL", "transactionId": "MP1", "receiptUrl": "https://x.invalid",
        "paymentData": json.dumps({"amount": 974, "productId": "vcrm-1", "phoneNumber": "600000000"}),
        "payload": json.dumps({
            "businessId": "biz", "paymentId": "2140600222", "amount": "1000",
            "status": "SUCCESS", "productId": "vcrm-1", "phoneNumber": "600000000",
        }),
    })
    assert response.normalized_status == TaraTransactionStatus.SUCCESS
    assert response.product_id == "vcrm-1"
    assert response.payment_id == "2140600222"
    assert response.business_id == "biz"
    assert response.amount == Decimal("1000")  # gross from payload, not the net 974 from paymentData
    assert "600000000" not in response.model_dump_json()  # personal data is never kept


def test_not_found_answer_is_unknown_not_failure():
    response = TaraPaymentStatusResponse.model_validate({"status": "ERROR", "message": "PAYMENT_FOR_TRANSACTION_NOT_FOUND"})
    assert response.normalized_status == TaraTransactionStatus.UNKNOWN
    assert response.product_id is None and response.amount is None


def test_unparseable_payload_yields_no_binding_fields():
    response = TaraPaymentStatusResponse.model_validate({"status": "SUCCESS", "payload": "{not json"})
    assert response.product_id is None and response.amount is None


def test_numeric_payment_id_is_normalized_to_string():
    response = TaraPaymentStatusResponse.model_validate(
        {"status": "SUCCESS", "payload": {"paymentId": 2140600222, "productId": "vcrm-1", "amount": 1000}},
    )
    assert response.payment_id == "2140600222"
    assert response.amount == Decimal("1000")
