import json
from decimal import Decimal
from unittest.mock import Mock

import pytest

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import Installment, Order, PaymentAttempt, PaymentPlan, TaraConfig
from apps.payments.services import (
    InstallmentService,
    OrderService,
    PaymentAttemptService,
    PaymentCreditService,
    WebhookProcessingService,
    WebhookRejectedError,
)
from integrations.payments.tara.schemas import TaraPaymentStatusResponse, TaraTransactionStatusResponse
from shared.security import encrypt_value

pytestmark = pytest.mark.django_db


@pytest.fixture
def active_config():
    return TaraConfig.objects.create(
        name="Main", business_id="biz_123", is_active=True,
        api_key=encrypt_value("key"), webhook_secret=encrypt_value("secret"),
    )


@pytest.fixture
def order():
    course = Course.objects.create(cf_course_id="crs_1", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code="bootcamp-1x", name="Bootcamp", course=course,
        installment_count=1, installment_amount=Decimal("100000.00"), is_active=True,
    )
    contact = Contact.objects.create(email="buyer@example.com")
    created, _ = OrderService().create_order(contact, plan.id, "idem-webhook-tests")
    return created


@pytest.fixture
def attempt(order):
    installment = order.installments.get()
    a = PaymentAttemptService().create_attempt(installment)
    PaymentAttemptService().transition(a, PaymentAttempt.Status.LINK_CREATED)
    return a


def body(**overrides):
    payload = {"businessId": "biz_123", "productId": "attempt-1", "status": "SUCCESS"}
    payload.update(overrides)
    return json.dumps(payload).encode()


def payment_not_found():
    return TaraPaymentStatusResponse.model_validate({"status": "ERROR", "message": "TRANSACTION_NOT_FOUND"})


def payment_status(product_id, payment_id, status="SUCCESS", amount="100000", business_id="biz_123"):
    """Shape captured from production on 2026-09-25 (values replaced) — see TaraPaymentStatusResponse."""
    return TaraPaymentStatusResponse.model_validate({
        "status": status, "message": "API_ORDER_SUCESSFULL", "transactionId": "MP-1",
        "paymentData": json.dumps({"amount": 974, "productId": product_id, "phoneNumber": "600000000"}),
        "receiptUrl": "https://example.invalid/receipt",
        "payload": json.dumps({
            "businessId": business_id, "paymentId": payment_id, "amount": amount,
            "status": status, "productId": product_id, "phoneNumber": "600000000",
        }),
    })


def status_response(product_id, status="SUCCESS"):
    return TaraTransactionStatusResponse.model_validate({"productId": product_id, "status": status, "message": "ok"})


# --- Parsing / structural rejection ---

def test_malformed_json_rejected():
    with pytest.raises(WebhookRejectedError):
        WebhookProcessingService().process_webhook(b"not json{{{")


def test_array_payload_rejected():
    with pytest.raises(WebhookRejectedError):
        WebhookProcessingService().process_webhook(b"[1, 2, 3]")


def test_string_payload_rejected():
    with pytest.raises(WebhookRejectedError):
        WebhookProcessingService().process_webhook(b'"just a string"')


def test_null_payload_rejected():
    with pytest.raises(WebhookRejectedError):
        WebhookProcessingService().process_webhook(b"null")


def test_oversized_body_rejected():
    huge = json.dumps({"businessId": "biz_123", "productId": "x", "status": "SUCCESS", "padding": "a" * 100_000}).encode()
    with pytest.raises(WebhookRejectedError):
        WebhookProcessingService().process_webhook(huge)


def test_missing_correlation_fields_rejected():
    payload = json.dumps({"businessId": "biz_123", "status": "SUCCESS"}).encode()  # no paymentId, no productId
    with pytest.raises(WebhookRejectedError):
        WebhookProcessingService().process_webhook(payload)


def test_mobile_money_style_payload_without_product_id_is_accepted_structurally(active_config):
    """The documented Mobile Money payload omits productId but has paymentId — must not be rejected outright."""
    payload = json.dumps({"businessId": "biz_123", "paymentId": "pay-1", "phoneNumber": "696717597", "status": "SUCCESS"}).encode()
    event = WebhookProcessingService().process_webhook(payload)
    assert event.processing_status in (
        event.ProcessingStatus.UNCORRELATED, event.ProcessingStatus.REJECTED, event.ProcessingStatus.FAILED, event.ProcessingStatus.PROCESSED,
    )


# --- businessId validation ---

def test_no_active_config_is_rejected_generically():
    event = WebhookProcessingService().process_webhook(body())
    assert event.processing_status == event.ProcessingStatus.REJECTED
    assert event.failure_category == event.FailureCategory.BUSINESS_ID_MISMATCH


def test_mismatched_business_id_is_rejected(active_config):
    event = WebhookProcessingService().process_webhook(body(businessId="someone-elses-business"))
    assert event.processing_status == event.ProcessingStatus.REJECTED
    assert event.failure_category == event.FailureCategory.BUSINESS_ID_MISMATCH


def test_matching_business_id_proceeds_to_correlation(active_config, mocker):
    mock_client = Mock()
    mock_client.check_payment_status.return_value = payment_not_found()
    mock_client.check_transaction_status.return_value = status_response("nonexistent-product")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    event = WebhookProcessingService().process_webhook(body(productId="nonexistent-product"))
    assert event.processing_status == event.ProcessingStatus.UNCORRELATED
    mock_client.check_transaction_status.assert_not_called()  # unknown productId -> no Tara call at all


# --- Correlation: exact tara_product_id only ---

def test_unknown_product_id_never_calls_tara_status(active_config, mocker):
    mock_client = Mock()
    mock_client.check_payment_status.return_value = payment_not_found()
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    event = WebhookProcessingService().process_webhook(body(productId="never-heard-of-this"))

    assert event.processing_status == event.ProcessingStatus.UNCORRELATED
    assert event.failure_category == event.FailureCategory.UNKNOWN_PRODUCT_ID
    mock_client.check_transaction_status.assert_not_called()


def test_exact_product_id_match_invokes_status_verification(active_config, attempt, mocker):
    mock_client = Mock()
    mock_client.check_payment_status.return_value = payment_not_found()
    mock_client.check_transaction_status.return_value = status_response(attempt.tara_product_id)
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    event = WebhookProcessingService().process_webhook(body(productId=attempt.tara_product_id))

    mock_client.check_transaction_status.assert_called_once_with(attempt.tara_product_id)
    assert event.payment_attempt_id == attempt.id


def test_phone_amount_collection_id_cannot_correlate(active_config, attempt, mocker):
    """A webhook with matching phone/amount/collectionId but NO productId/paymentId matching a known attempt must not correlate."""
    mock_client = Mock()
    mock_client.check_payment_status.return_value = payment_not_found()
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    payload = json.dumps({
        "businessId": "biz_123", "paymentId": "unrelated-payment-id",
        "phoneNumber": "696717597", "amount": str(attempt.expected_amount), "collectionId": "27731",
        "status": "SUCCESS",
    }).encode()
    event = WebhookProcessingService().process_webhook(payload)

    assert event.processing_status == event.ProcessingStatus.UNCORRELATED
    assert event.payment_attempt_id is None
    mock_client.check_transaction_status.assert_not_called()


# --- Verification outcomes ---

def test_verified_success_credits_payment(active_config, attempt, mocker):
    mock_client = Mock()
    mock_client.check_payment_status.return_value = payment_not_found()
    mock_client.check_transaction_status.return_value = status_response(attempt.tara_product_id, "SUCCESS")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    WebhookProcessingService().process_webhook(body(productId=attempt.tara_product_id, paymentId="pay-1"))

    attempt.refresh_from_db()
    installment = Installment.objects.get(pk=attempt.installment_id)
    assert attempt.status == PaymentAttempt.Status.SUCCEEDED
    assert installment.status == Installment.Status.PAID


def test_verified_failure_applies_failed_state(active_config, attempt, mocker):
    mock_client = Mock()
    mock_client.check_payment_status.return_value = payment_not_found()
    mock_client.check_transaction_status.return_value = status_response(attempt.tara_product_id, "FAILURE")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    event = WebhookProcessingService().process_webhook(body(productId=attempt.tara_product_id, status="FAILURE"))

    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.FAILED
    assert event.verification_result == event.VerificationResult.FAILURE


def test_verified_pending_remains_non_final(active_config, attempt, mocker):
    mock_client = Mock()
    mock_client.check_payment_status.return_value = payment_not_found()
    mock_client.check_transaction_status.return_value = status_response(attempt.tara_product_id, "PENDING")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    event = WebhookProcessingService().process_webhook(body(productId=attempt.tara_product_id, status="PENDING"))

    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.LINK_CREATED  # unchanged, not final
    assert event.verification_result == event.VerificationResult.PENDING


def test_unknown_status_remains_non_final(active_config, attempt, mocker):
    mock_client = Mock()
    mock_client.check_payment_status.return_value = payment_not_found()
    mock_client.check_transaction_status.return_value = status_response(attempt.tara_product_id, "SOME_NEW_STATUS")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    WebhookProcessingService().process_webhook(body(productId=attempt.tara_product_id))

    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.LINK_CREATED  # unchanged


def test_product_id_mismatch_from_status_api_is_rejected(active_config, attempt, mocker):
    mock_client = Mock()
    mock_client.check_payment_status.return_value = payment_not_found()
    mock_client.check_transaction_status.return_value = status_response("a-totally-different-product-id", "SUCCESS")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    event = WebhookProcessingService().process_webhook(body(productId=attempt.tara_product_id))

    attempt.refresh_from_db()
    assert attempt.status != PaymentAttempt.Status.SUCCEEDED  # never credited
    assert event.failure_category == event.FailureCategory.MALFORMED


def test_timeout_from_status_api_remains_recoverable(active_config, attempt, mocker):
    from integrations.payments.tara.exceptions import TaraTimeoutError
    mock_client = Mock()
    mock_client.check_payment_status.return_value = payment_not_found()
    mock_client.check_transaction_status.side_effect = TaraTimeoutError("timed out")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    event = WebhookProcessingService().process_webhook(body(productId=attempt.tara_product_id))

    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.LINK_CREATED  # unchanged
    assert event.processing_status == event.ProcessingStatus.FAILED  # recoverable, for reconciliation
    assert event.failure_category == event.FailureCategory.PROVIDER_LOOKUP_INDETERMINATE


def test_connection_loss_from_status_api_remains_recoverable(active_config, attempt, mocker):
    from integrations.payments.tara.exceptions import TaraConnectionError
    mock_client = Mock()
    mock_client.check_payment_status.return_value = payment_not_found()
    mock_client.check_transaction_status.side_effect = TaraConnectionError("no connection")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    event = WebhookProcessingService().process_webhook(body(productId=attempt.tara_product_id))

    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.LINK_CREATED
    assert event.processing_status == event.ProcessingStatus.FAILED


def test_5xx_from_status_api_remains_recoverable(active_config, attempt, mocker):
    from integrations.payments.tara.exceptions import TaraServerError
    mock_client = Mock()
    mock_client.check_payment_status.return_value = payment_not_found()
    mock_client.check_transaction_status.side_effect = TaraServerError("server error", status_code=503)
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    event = WebhookProcessingService().process_webhook(body(productId=attempt.tara_product_id))

    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.LINK_CREATED
    assert event.processing_status == event.ProcessingStatus.FAILED


def test_malformed_status_response_remains_recoverable(active_config, attempt, mocker):
    from integrations.payments.tara.exceptions import TaraMalformedResponseError
    mock_client = Mock()
    mock_client.check_payment_status.return_value = payment_not_found()
    mock_client.check_transaction_status.side_effect = TaraMalformedResponseError("bad shape")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    event = WebhookProcessingService().process_webhook(body(productId=attempt.tara_product_id))

    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.LINK_CREATED
    assert event.processing_status == event.ProcessingStatus.FAILED


def test_configuration_failure_causes_no_payment_transition(attempt):
    """No active TaraConfig -> businessId check fails first -> REJECTED, never reaches status verification, never transitions the attempt."""
    event = WebhookProcessingService().process_webhook(body(productId=attempt.tara_product_id))
    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.LINK_CREATED
    assert event.processing_status == event.ProcessingStatus.REJECTED


# --- Webhook amount mismatch ---

def test_webhook_amount_mismatch_prevents_automatic_credit(active_config, attempt, mocker):
    mock_client = Mock()
    mock_client.check_payment_status.return_value = payment_not_found()
    mock_client.check_transaction_status.return_value = status_response(attempt.tara_product_id, "SUCCESS")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    wrong_amount = attempt.expected_amount + Decimal("1.00")
    event = WebhookProcessingService().process_webhook(body(productId=attempt.tara_product_id, amount=str(wrong_amount)))

    attempt.refresh_from_db()
    installment = Installment.objects.get(pk=attempt.installment_id)
    assert attempt.status != PaymentAttempt.Status.SUCCEEDED
    assert installment.status != Installment.Status.PAID
    assert event.failure_category == event.FailureCategory.AMOUNT_MISMATCH


def test_webhook_amount_matching_expected_does_not_block_credit(active_config, attempt, mocker):
    mock_client = Mock()
    mock_client.check_payment_status.return_value = payment_not_found()
    mock_client.check_transaction_status.return_value = status_response(attempt.tara_product_id, "SUCCESS")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    WebhookProcessingService().process_webhook(body(productId=attempt.tara_product_id, amount=str(attempt.expected_amount)))

    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.SUCCEEDED


# --- Credentials never leaked ---

def test_no_secrets_in_logs(active_config, attempt, mocker, caplog):
    import logging
    mock_client = Mock()
    mock_client.check_payment_status.return_value = payment_not_found()
    mock_client.check_transaction_status.return_value = status_response(attempt.tara_product_id, "SUCCESS")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)
    caplog.set_level(logging.INFO)

    WebhookProcessingService().process_webhook(body(productId=attempt.tara_product_id))

    for record in caplog.records:
        message = record.getMessage().lower()
        assert "apikey" not in message
        assert "secret" not in message


def test_uncorrelated_event_cannot_provision_access(active_config, mocker):
    mock_provisioning = mocker.patch("apps.provisioning.services.ProvisioningService")

    event = WebhookProcessingService().process_webhook(body(productId="never-seen-before"))

    assert event.processing_status == event.ProcessingStatus.UNCORRELATED
    mock_provisioning.assert_not_called()


def test_correlated_success_never_calls_clickfunnels_directly(active_config, attempt, mocker):
    """
    Phase 6->7 boundary: webhook processing legitimately creates a durable
    ProvisioningRequest row when the order becomes access-eligible (Phase 7,
    docs/TARA_INTEGRATION_PROJECT.md) — that's a DB write only, inside the
    same transaction as the payment credit. It must never call ClickFunnels
    (ProvisioningService.execute()) directly from this path; that's the
    separate worker/management command's job, run outside this transaction.
    """
    from apps.provisioning.models import ProvisioningRequest

    mock_client = Mock()
    mock_client.check_payment_status.return_value = payment_not_found()
    mock_client.check_transaction_status.return_value = status_response(attempt.tara_product_id, "SUCCESS")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)
    mock_execute = mocker.patch("apps.provisioning.services.ProvisioningService.execute")

    WebhookProcessingService().process_webhook(body(productId=attempt.tara_product_id))

    mock_execute.assert_not_called()
    request = ProvisioningRequest.objects.get(order=attempt.installment.order)
    assert request.status == ProvisioningRequest.Status.PENDING


# --- FAILURE webhook, then a SUCCESS webhook for the same productId ---

def test_success_webhook_after_failure_webhook_credits_payment(active_config, attempt, mocker):
    """Regression: previously raised InvalidStateTransitionError (500) and the verified payment was lost."""
    mock_client = Mock()
    mock_client.check_payment_status.return_value = payment_not_found()
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    mock_client.check_transaction_status.return_value = status_response(attempt.tara_product_id, "FAILURE")
    WebhookProcessingService().process_webhook(body(productId=attempt.tara_product_id, status="FAILURE"))
    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.FAILED

    mock_client.check_transaction_status.return_value = status_response(attempt.tara_product_id, "SUCCESS")
    event = WebhookProcessingService().process_webhook(
        body(productId=attempt.tara_product_id, paymentId="pay-late", status="SUCCESS"),
    )

    attempt.refresh_from_db()
    installment = Installment.objects.get(pk=attempt.installment_id)
    assert event.processing_status == event.ProcessingStatus.PROCESSED
    assert event.verification_result == event.VerificationResult.SUCCESS
    assert attempt.status == PaymentAttempt.Status.SUCCEEDED
    assert installment.status == Installment.Status.PAID


def test_success_webhook_for_already_paid_installment_is_flagged(active_config, attempt, mocker):
    installment = attempt.installment
    PaymentCreditService().apply_verified_failure(attempt.id)
    newer = PaymentAttemptService().create_attempt(installment)
    PaymentAttemptService().transition(newer, PaymentAttempt.Status.LINK_CREATED)
    PaymentCreditService().apply_verified_success(newer.id)

    mock_client = Mock()
    mock_client.check_payment_status.return_value = payment_not_found()
    mock_client.check_transaction_status.return_value = status_response(attempt.tara_product_id, "SUCCESS")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    event = WebhookProcessingService().process_webhook(body(productId=attempt.tara_product_id, paymentId="pay-dup"))

    attempt.refresh_from_db()
    assert event.processing_status == event.ProcessingStatus.FAILED
    assert event.failure_category == event.FailureCategory.DUPLICATE_PAYMENT
    assert attempt.status == PaymentAttempt.Status.FAILED


def test_success_webhook_for_cancelled_installment_is_recorded_not_raised(active_config, attempt, mocker):
    installment = attempt.installment
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.EXPIRED)
    InstallmentService().transition(installment, Installment.Status.CANCELLED)

    mock_client = Mock()
    mock_client.check_payment_status.return_value = payment_not_found()
    mock_client.check_transaction_status.return_value = status_response(attempt.tara_product_id, "SUCCESS")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    event = WebhookProcessingService().process_webhook(body(productId=attempt.tara_product_id))

    attempt.refresh_from_db()
    assert event.processing_status == event.ProcessingStatus.FAILED
    assert event.failure_category == event.FailureCategory.INVALID_STATE_TRANSITION
    assert attempt.status == PaymentAttempt.Status.EXPIRED  # rolled back, untouched


# --- Verification by Tara paymentId (production behaviour, 2026-09-25) ---

def mock_client_for(mocker, **kwargs):
    mock_client = Mock(business_id="biz_123")
    mock_client.check_payment_status.return_value = payment_not_found()
    mock_client.check_transaction_status.return_value = TaraTransactionStatusResponse.model_validate(
        {"productId": "irrelevant", "status": "ERROR"},
    )
    for name, value in kwargs.items():
        getattr(mock_client, name).return_value = value
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)
    return mock_client


def test_payment_verified_by_payment_id_when_product_id_lookup_finds_nothing(active_config, attempt, mocker):
    """The production case: productId lookup says NOT_FOUND, paymentId lookup confirms productId + amount."""
    client = mock_client_for(mocker, check_payment_status=payment_status(attempt.tara_product_id, "2140600222"))

    event = WebhookProcessingService().process_webhook(
        body(productId=attempt.tara_product_id, paymentId="2140600222", status="SUCCESS"),
    )

    attempt.refresh_from_db()
    installment = Installment.objects.get(pk=attempt.installment_id)
    assert event.processing_status == event.ProcessingStatus.PROCESSED
    assert attempt.status == PaymentAttempt.Status.SUCCEEDED
    assert attempt.tara_payment_id == "2140600222"
    assert installment.status == Installment.Status.PAID
    assert installment.paid_amount == Decimal("100000")  # gross amount from Tara, not the webhook
    client.check_payment_status.assert_called_once_with("2140600222")
    client.check_transaction_status.assert_not_called()


def test_forged_payment_id_of_another_product_is_never_credited(active_config, attempt, mocker):
    """Attacker sends our productId with the paymentId of someone else's real payment."""
    mock_client_for(mocker, check_payment_status=payment_status("1788257779__other-product", "1381942514"))

    event = WebhookProcessingService().process_webhook(
        body(productId=attempt.tara_product_id, paymentId="1381942514", status="SUCCESS"),
    )

    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.LINK_CREATED
    assert event.processing_status == event.ProcessingStatus.FAILED
    assert event.failure_category == event.FailureCategory.PROVIDER_PRODUCT_MISMATCH


def test_payment_of_another_business_is_never_credited(active_config, attempt, mocker):
    mock_client_for(mocker, check_payment_status=payment_status(attempt.tara_product_id, "p-1", business_id="other-biz"))

    event = WebhookProcessingService().process_webhook(body(productId=attempt.tara_product_id, paymentId="p-1"))

    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.LINK_CREATED
    assert event.failure_category == event.FailureCategory.PROVIDER_PRODUCT_MISMATCH


def test_verified_amount_differing_from_expected_is_not_credited(active_config, attempt, mocker):
    mock_client_for(mocker, check_payment_status=payment_status(attempt.tara_product_id, "p-2", amount="100"))

    event = WebhookProcessingService().process_webhook(body(productId=attempt.tara_product_id, paymentId="p-2"))

    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.LINK_CREATED
    assert event.failure_category == event.FailureCategory.AMOUNT_MISMATCH


def test_payment_id_lookup_failure_verdict_applies_failure(active_config, attempt, mocker):
    mock_client_for(mocker, check_payment_status=payment_status(attempt.tara_product_id, "p-3", status="FAILURE"))

    WebhookProcessingService().process_webhook(body(productId=attempt.tara_product_id, paymentId="p-3", status="FAILURE"))

    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.FAILED


def test_paypal_style_webhook_without_real_payment_id_stays_indeterminate(active_config, attempt, mocker):
    """PayPal webhooks carry our productId as paymentId and neither lookup finds them — never credited blindly."""
    client = mock_client_for(mocker)
    client.check_transaction_status.side_effect = __import__(
        "integrations.payments.tara.exceptions", fromlist=["TaraMalformedResponseError"],
    ).TaraMalformedResponseError("not found")

    event = WebhookProcessingService().process_webhook(
        body(productId=attempt.tara_product_id, paymentId=attempt.tara_product_id),
    )

    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.LINK_CREATED
    assert event.failure_category == event.FailureCategory.PROVIDER_LOOKUP_INDETERMINATE
    client.check_payment_status.assert_not_called()  # own productId is not used as a paymentId lookup key
