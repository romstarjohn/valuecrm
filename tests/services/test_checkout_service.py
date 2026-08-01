from decimal import Decimal
from unittest.mock import Mock

import pytest
from django.core import signing

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import Installment, Order, PaymentAttempt, PaymentPlan
from apps.payments.services import (
    CheckoutConfigurationError,
    CheckoutError,
    CheckoutService,
    InstallmentService,
    OrderService,
)
from integrations.payments.tara.exceptions import (
    TaraClientError,
    TaraConnectionError,
    TaraMalformedResponseError,
    TaraProviderBusinessError,
    TaraServerError,
    TaraTimeoutError,
)
from integrations.payments.tara.schemas import TaraPaymentLinkResponse, TaraTransactionStatusResponse

pytestmark = pytest.mark.django_db


@pytest.fixture
def course():
    return Course.objects.create(cf_course_id="crs_1", name="Bootcamp", workspace_id="ws_1")


@pytest.fixture
def plan(course):
    return PaymentPlan.objects.create(
        code="bootcamp-1x", name="Bootcamp", course=course, installment_count=1,
        installment_amount=Decimal("100000.00"), is_active=True,
    )


@pytest.fixture
def contact():
    return Contact.objects.create(email="buyer@example.com", phone="+15550001111")


@pytest.fixture
def service():
    return CheckoutService()


def success_response(**overrides):
    data = {"status": "success", "message": "ok", "generalLink": "https://taramoney.com/pay/abc123"}
    data.update(overrides)
    return TaraPaymentLinkResponse.model_validate(data)


# --- Configuration ---

def test_missing_public_base_url_raises_configuration_error(service, contact, plan, settings):
    settings.PUBLIC_BASE_URL = ""
    with pytest.raises(CheckoutConfigurationError):
        service.start_checkout(contact, plan.id, "idem-1")


def test_non_https_public_base_url_raises_configuration_error(service, contact, plan, settings):
    settings.PUBLIC_BASE_URL = "http://checkout.example.com"
    with pytest.raises(CheckoutConfigurationError):
        service.start_checkout(contact, plan.id, "idem-1")


# --- Happy path ---

def test_successful_checkout_creates_order_installment_attempt_and_returns_link(service, contact, plan, mocker):
    mock_client = Mock()
    mock_client.create_payment_link.return_value = success_response()
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    result = service.start_checkout(contact, plan.id, "idem-1")

    assert result.status == "link_ready"
    assert result.checkout_url == "https://taramoney.com/pay/abc123"
    order = Order.objects.get()
    installment = order.installments.get()
    attempt = installment.payment_attempts.get()
    assert attempt.status == PaymentAttempt.Status.LINK_CREATED
    assert attempt.general_link == "https://taramoney.com/pay/abc123"


def test_tara_called_with_authoritative_data_not_browser_input(service, contact, plan, mocker):
    mock_client = Mock()
    mock_client.create_payment_link.return_value = success_response()
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    service.start_checkout(contact, plan.id, "idem-1")

    call_kwargs = mock_client.create_payment_link.call_args.kwargs
    attempt = PaymentAttempt.objects.get()
    assert call_kwargs["product_id"] == attempt.tara_product_id
    assert call_kwargs["product_price"] == Decimal("100000.00")  # from Installment, not any browser value
    assert call_kwargs["web_hook_url"] == "https://checkout.example.com/api/tara/webhook/"
    assert call_kwargs["return_url"].startswith("https://checkout.example.com/checkout/status/")
    assert "api_key" not in call_kwargs
    assert "business_id" not in call_kwargs


def test_return_url_contains_only_opaque_signed_reference(service, contact, plan, mocker):
    """
    The URL path legitimately contains the literal word "status" (the
    checkout_status route name, apps/payments/urls.py) — that's fine. What
    must never appear is an actual payment status VALUE, a query string, or
    any customer/course/amount data.
    """
    mock_client = Mock()
    mock_client.create_payment_link.return_value = success_response()
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    service.start_checkout(contact, plan.id, "idem-1")
    return_url = mock_client.create_payment_link.call_args.kwargs["return_url"]

    assert contact.email not in return_url
    assert "?" not in return_url  # no query string at all
    assert "amount" not in return_url.lower()
    assert "success" not in return_url.lower()
    assert "paid" not in return_url.lower()
    assert "course" not in return_url.lower()
    assert plan.name.lower() not in return_url.lower()


# --- Idempotency ---

def test_repeated_checkout_reuses_stored_link_no_second_tara_call(service, contact, plan, mocker):
    mock_client = Mock()
    mock_client.create_payment_link.return_value = success_response()
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    first = service.start_checkout(contact, plan.id, "idem-1")
    second = service.start_checkout(contact, plan.id, "idem-1")

    assert first.order_reference == second.order_reference
    assert second.checkout_url == "https://taramoney.com/pay/abc123"
    mock_client.create_payment_link.assert_called_once()  # not called again
    assert Order.objects.count() == 1
    assert PaymentAttempt.objects.count() == 1


def test_conflicting_idempotency_key_reuse_rejected(service, contact, plan, mocker):
    mock_client = Mock()
    mock_client.create_payment_link.return_value = success_response()
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    service.start_checkout(contact, plan.id, "idem-1")
    other_contact = Contact.objects.create(email="other@example.com")
    with pytest.raises(Exception):  # OrderCreationError, bubbled up from OrderService
        service.start_checkout(other_contact, plan.id, "idem-1")


# --- Provider outcomes ---

def test_timeout_becomes_unknown_no_new_attempt(service, contact, plan, mocker):
    mock_client = Mock()
    mock_client.create_payment_link.side_effect = TaraTimeoutError("timed out")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    result = service.start_checkout(contact, plan.id, "idem-1")

    assert result.status == "verification_pending"
    attempt = PaymentAttempt.objects.get()
    assert attempt.status == PaymentAttempt.Status.UNKNOWN
    assert PaymentAttempt.objects.count() == 1


def test_connection_loss_becomes_unknown(service, contact, plan, mocker):
    mock_client = Mock()
    mock_client.create_payment_link.side_effect = TaraConnectionError("no connection")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    result = service.start_checkout(contact, plan.id, "idem-1")

    assert result.status == "verification_pending"
    assert PaymentAttempt.objects.get().status == PaymentAttempt.Status.UNKNOWN


def test_5xx_handled_as_unknown_not_failed(service, contact, plan, mocker):
    mock_client = Mock()
    mock_client.create_payment_link.side_effect = TaraServerError("server error", status_code=503)
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    result = service.start_checkout(contact, plan.id, "idem-1")

    assert result.status == "verification_pending"
    assert PaymentAttempt.objects.get().status == PaymentAttempt.Status.UNKNOWN


def test_definitive_business_failure_becomes_failed(service, contact, plan, mocker):
    mock_client = Mock()
    mock_client.create_payment_link.side_effect = TaraProviderBusinessError("unsuccessful")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    result = service.start_checkout(contact, plan.id, "idem-1")

    assert result.status == "failed"
    assert PaymentAttempt.objects.get().status == PaymentAttempt.Status.FAILED


def test_4xx_client_error_becomes_failed(service, contact, plan, mocker):
    mock_client = Mock()
    mock_client.create_payment_link.side_effect = TaraClientError("bad request", status_code=400)
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    result = service.start_checkout(contact, plan.id, "idem-1")

    assert result.status == "failed"
    assert PaymentAttempt.objects.get().status == PaymentAttempt.Status.FAILED


def test_malformed_response_handled_safely_as_unknown(service, contact, plan, mocker):
    mock_client = Mock()
    mock_client.create_payment_link.side_effect = TaraMalformedResponseError("bad shape")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    result = service.start_checkout(contact, plan.id, "idem-1")

    assert result.status == "verification_pending"
    assert PaymentAttempt.objects.get().status == PaymentAttempt.Status.UNKNOWN


def test_no_automatic_retry_after_failure(service, contact, plan, mocker):
    """A second start_checkout call after a definitive FAILED must not call Tara again automatically."""
    mock_client = Mock()
    mock_client.create_payment_link.side_effect = TaraProviderBusinessError("unsuccessful")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    service.start_checkout(contact, plan.id, "idem-1")
    result = service.start_checkout(contact, plan.id, "idem-1")

    assert result.status == "failed"
    mock_client.create_payment_link.assert_called_once()  # still only the first call
    assert PaymentAttempt.objects.count() == 1


# --- Repeated request after UNKNOWN: check status first, don't blindly recreate ---

def test_unknown_attempt_rechecked_via_status_not_recreated(service, contact, plan, mocker):
    mock_client = Mock()
    mock_client.create_payment_link.side_effect = TaraTimeoutError("timed out")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)
    service.start_checkout(contact, plan.id, "idem-1")
    assert PaymentAttempt.objects.get().status == PaymentAttempt.Status.UNKNOWN

    mock_client.check_transaction_status.return_value = TaraTransactionStatusResponse.model_validate({
        "productId": PaymentAttempt.objects.get().tara_product_id, "status": "SUCCESS", "message": "paid",
    })

    result = service.start_checkout(contact, plan.id, "idem-1")

    assert result.status == "succeeded"
    mock_client.create_payment_link.assert_called_once()  # never called a second time
    mock_client.check_transaction_status.assert_called_once()
    assert PaymentAttempt.objects.get().status == PaymentAttempt.Status.SUCCEEDED
    assert PaymentAttempt.objects.count() == 1  # no new attempt created


def test_unknown_attempt_recheck_still_pending_stays_unknown(service, contact, plan, mocker):
    mock_client = Mock()
    mock_client.create_payment_link.side_effect = TaraTimeoutError("timed out")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)
    service.start_checkout(contact, plan.id, "idem-1")

    mock_client.check_transaction_status.return_value = TaraTransactionStatusResponse.model_validate({
        "productId": PaymentAttempt.objects.get().tara_product_id, "status": "PENDING", "message": "waiting",
    })

    result = service.start_checkout(contact, plan.id, "idem-1")

    assert result.status == "verification_pending"
    assert PaymentAttempt.objects.get().status == PaymentAttempt.Status.UNKNOWN  # not moved to PENDING
    assert PaymentAttempt.objects.count() == 1


def test_unknown_attempt_recheck_itself_times_out_stays_pending(service, contact, plan, mocker):
    mock_client = Mock()
    mock_client.create_payment_link.side_effect = TaraTimeoutError("timed out")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)
    service.start_checkout(contact, plan.id, "idem-1")

    mock_client.check_transaction_status.side_effect = TaraTimeoutError("timed out again")

    result = service.start_checkout(contact, plan.id, "idem-1")

    assert result.status == "verification_pending"
    assert PaymentAttempt.objects.get().status == PaymentAttempt.Status.UNKNOWN
    assert PaymentAttempt.objects.count() == 1


# --- Redirect URL safety ---

def test_disallowed_redirect_host_rejected(service, contact, plan, mocker):
    mock_client = Mock()
    mock_client.create_payment_link.return_value = success_response(generalLink="https://evil.example/steal")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    result = service.start_checkout(contact, plan.id, "idem-1")

    assert result.status == "verification_pending"
    assert result.checkout_url is None


def test_username_password_in_url_rejected(service, contact, plan, mocker):
    mock_client = Mock()
    mock_client.create_payment_link.return_value = success_response(generalLink="https://user:pass@taramoney.com/pay/abc")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    result = service.start_checkout(contact, plan.id, "idem-1")

    assert result.checkout_url is None


def test_non_default_port_rejected(service, contact, plan, mocker):
    mock_client = Mock()
    mock_client.create_payment_link.return_value = success_response(generalLink="https://taramoney.com:8443/pay/abc")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    result = service.start_checkout(contact, plan.id, "idem-1")

    assert result.checkout_url is None


def test_non_https_general_link_rejected(service, contact, plan, mocker):
    mock_client = Mock()
    mock_client.create_payment_link.return_value = TaraPaymentLinkResponse.model_validate({
        "status": "success", "message": "ok",
    })  # no generalLink at all
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    result = service.start_checkout(contact, plan.id, "idem-1")

    assert result.checkout_url is None
    assert result.status == "verification_pending"


def test_whatsapp_telegram_dikalo_sms_never_used_as_checkout_url(service, contact, plan, mocker):
    mock_client = Mock()
    mock_client.create_payment_link.return_value = TaraPaymentLinkResponse.model_validate({
        "status": "success", "message": "ok",
        "whatsappLink": "https://wa.me/123", "telegramLink": "https://t.me/123",
        "dikaloLink": "https://dikalo.example/123", "smsLink": "sms:+123",
        # deliberately no generalLink
    })
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    result = service.start_checkout(contact, plan.id, "idem-1")

    assert result.checkout_url is None  # never falls back to whatsapp/telegram/dikalo/sms


# --- Phase 6 correction: checkout redirect allowlist ---
#
# Phase 5's original allowlist was wrong — it included www.dklo.co/dklo.co
# (TaraClient.BASE_URL, the API host) as if it were also a valid checkout
# REDIRECT host. It never was: Tara's documented sample generalLink/cardLink
# values point at taramoney.com, a different domain entirely. These tests
# pin the corrected behavior and guard against the two concepts being
# conflated again.

def test_documented_taramoney_general_link_is_accepted(service, contact, plan, mocker):
    mock_client = Mock()
    mock_client.create_payment_link.return_value = success_response(generalLink="https://taramoney.com/pay/xyz789")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    result = service.start_checkout(contact, plan.id, "idem-1")

    assert result.checkout_url == "https://taramoney.com/pay/xyz789"


def test_subdomain_lookalike_host_rejected(service, contact, plan, mocker):
    """taramoney.com.attacker.example has taramoney.com as a SUBDOMAIN label, not the actual host — a classic lookalike-domain attack."""
    mock_client = Mock()
    mock_client.create_payment_link.return_value = success_response(generalLink="https://taramoney.com.attacker.example/pay/xyz")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    result = service.start_checkout(contact, plan.id, "idem-1")

    assert result.checkout_url is None


def test_attacker_prefix_lookalike_host_rejected(service, contact, plan, mocker):
    """attacker-taramoney.com is a different registered domain, not taramoney.com — must not pass a naive substring/suffix check."""
    mock_client = Mock()
    mock_client.create_payment_link.return_value = success_response(generalLink="https://attacker-taramoney.com/pay/xyz")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    result = service.start_checkout(contact, plan.id, "idem-1")

    assert result.checkout_url is None


def test_dklo_co_link_not_accepted_as_checkout_redirect(service, contact, plan, mocker):
    """dklo.co is the documented API host ONLY — never evidenced as a checkout-redirect host. Must not be trusted as one merely because it's a known Tara domain."""
    mock_client = Mock()
    mock_client.create_payment_link.return_value = success_response(generalLink="https://www.dklo.co/pay/abc123")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    result = service.start_checkout(contact, plan.id, "idem-1")

    assert result.checkout_url is None
    assert result.status == "verification_pending"


def test_non_https_taramoney_link_rejected(service, contact, plan, mocker):
    mock_client = Mock()
    mock_client.create_payment_link.return_value = success_response(generalLink="http://taramoney.com/pay/abc123")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    result = service.start_checkout(contact, plan.id, "idem-1")

    assert result.checkout_url is None


def test_userinfo_in_taramoney_link_rejected(service, contact, plan, mocker):
    mock_client = Mock()
    mock_client.create_payment_link.return_value = success_response(generalLink="https://user:pass@taramoney.com/pay/abc123")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    result = service.start_checkout(contact, plan.id, "idem-1")

    assert result.checkout_url is None


def test_unexpected_port_on_taramoney_link_rejected(service, contact, plan, mocker):
    mock_client = Mock()
    mock_client.create_payment_link.return_value = success_response(generalLink="https://taramoney.com:8443/pay/abc123")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    result = service.start_checkout(contact, plan.id, "idem-1")

    assert result.checkout_url is None


def test_unicode_lookalike_host_rejected(service, contact, plan, mocker):
    """xn--tararnoney (IDNA/punycode homoglyph-style lookalike) must not resolve to the real taramoney.com in the allowlist check."""
    mock_client = Mock()
    mock_client.create_payment_link.return_value = success_response(generalLink="https://tarаmoney.com/pay/abc123")  # Cyrillic а (U+0430)
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    result = service.start_checkout(contact, plan.id, "idem-1")

    assert result.checkout_url is None


def test_api_host_constant_and_checkout_host_allowlist_are_distinct(service):
    """Structural guard: the API host (TaraClient.BASE_URL) and the checkout-redirect allowlist must never be the same constant/set again."""
    from apps.payments.services import _ALLOWED_TARA_CHECKOUT_HOSTS
    from integrations.payments.tara.client import TaraClient

    assert "taramoney.com" in _ALLOWED_TARA_CHECKOUT_HOSTS
    assert "dklo.co" not in _ALLOWED_TARA_CHECKOUT_HOSTS
    assert "www.dklo.co" not in _ALLOWED_TARA_CHECKOUT_HOSTS
    assert "dklo.co" in TaraClient.BASE_URL  # confirms these really are different hosts, not accidentally equal


# --- Credentials never leaked ---

def test_credentials_absent_from_checkout_result(service, contact, plan, mocker):
    mock_client = Mock()
    mock_client.create_payment_link.return_value = success_response()
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)

    result = service.start_checkout(contact, plan.id, "idem-1")

    assert not hasattr(result, "api_key")
    assert not hasattr(result, "business_id")


# --- Signed reference ---

def test_signed_reference_round_trips(service, contact, plan):
    order, _ = OrderService().create_order(contact, plan.id, "idem-sig")
    token = service.build_signed_reference(order.reference)
    resolved = service.resolve_signed_reference(token)
    assert resolved.id == order.id


def test_tampered_signed_reference_rejected(service, contact, plan):
    order, _ = OrderService().create_order(contact, plan.id, "idem-sig2")
    token = service.build_signed_reference(order.reference)
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(CheckoutError):
        service.resolve_signed_reference(tampered)


def test_expired_signed_reference_rejected(service, contact, plan):
    order, _ = OrderService().create_order(contact, plan.id, "idem-sig3")
    token = service.build_signed_reference(order.reference)
    with pytest.raises(CheckoutError):
        service.resolve_signed_reference(token, max_age_seconds=-1)


def test_bare_order_reference_is_not_a_valid_token():
    """A UUID alone must never be accepted as authorization — only a correctly signed token."""
    service = CheckoutService()
    with pytest.raises(CheckoutError):
        service.resolve_signed_reference("00000000-0000-0000-0000-000000000000")
