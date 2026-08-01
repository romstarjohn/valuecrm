import uuid
from decimal import Decimal
from unittest.mock import Mock

import pytest
from django.urls import reverse

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import Order, PaymentAttempt, PaymentPlan
from integrations.payments.tara.schemas import TaraPaymentLinkResponse

pytestmark = pytest.mark.django_db


@pytest.fixture
def course():
    return Course.objects.create(cf_course_id="crs_1", name="Bootcamp", workspace_id="ws_1")


@pytest.fixture
def other_course():
    return Course.objects.create(cf_course_id="crs_2", name="Other Course", workspace_id="ws_1")


@pytest.fixture
def plan(course):
    return PaymentPlan.objects.create(
        code="bootcamp-1x", name="Bootcamp", course=course, currency=PaymentPlan.Currency.XAF,
        installment_count=1, installment_amount=Decimal("100000.00"), is_active=True,
    )


def checkout_url(plan):
    return reverse("payments:checkout_start", args=[plan.id])


def valid_payload(plan, idempotency_key=None, **overrides):
    payload = {
        "plan_id": plan.id,
        "idempotency_key": idempotency_key or str(uuid.uuid4()),
        "email": "buyer@example.com",
        "phone": "+237 600 000 000",
        "first_name": "Jane",
        "last_name": "Doe",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def mock_tara_success(mocker):
    mock_client = Mock()
    mock_client.create_payment_link.return_value = TaraPaymentLinkResponse.model_validate({
        "status": "success", "message": "ok", "generalLink": "https://taramoney.com/pay/abc123",
    })
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=mock_client)
    return mock_client


# --- Input trust ---

def test_submitted_amount_ignored(client, plan, mock_tara_success):
    """There is no 'amount' field on the form at all — any submitted value is simply discarded, never reaches the Order."""
    client.post(checkout_url(plan), data=valid_payload(plan, amount="1"))
    order = Order.objects.get()
    assert order.installment_amount == Decimal("100000.00")  # from the plan, not "1"


def test_submitted_course_ignored(client, plan, other_course, mock_tara_success):
    client.post(checkout_url(plan), data=valid_payload(plan, course_id=other_course.id))
    order = Order.objects.get()
    assert order.course_id == plan.course_id
    assert order.course_id != other_course.id


def test_submitted_currency_ignored(client, plan, mock_tara_success):
    client.post(checkout_url(plan), data=valid_payload(plan, currency="USD"))
    order = Order.objects.get()
    assert order.currency == "XAF"


def test_submitted_callback_url_ignored(client, plan, mock_tara_success):
    client.post(checkout_url(plan), data=valid_payload(plan, return_url="https://evil.example/steal", web_hook_url="https://evil.example/hook"))
    call_kwargs = mock_tara_success.create_payment_link.call_args.kwargs
    assert "evil.example" not in call_kwargs["return_url"]
    assert "evil.example" not in call_kwargs["web_hook_url"]


def test_submitted_business_id_and_api_key_ignored(client, plan, mock_tara_success):
    client.post(checkout_url(plan), data=valid_payload(plan, business_id="hacked-biz", api_key="stolen-key"))
    call_kwargs = mock_tara_success.create_payment_link.call_args.kwargs
    assert "business_id" not in call_kwargs
    assert "api_key" not in call_kwargs


def test_inactive_plan_id_in_form_rejected(client, plan, mock_tara_success):
    plan.is_active = False
    plan.save()
    response = client.post(checkout_url(plan), data=valid_payload(plan))
    assert response.status_code == 404  # get_object_or_404 in checkout_start itself


def test_plan_id_mismatch_between_url_and_form_is_rejected(client, plan, course, mock_tara_success):
    other_plan = PaymentPlan.objects.create(
        code="other-plan", name="Other Plan", course=course,
        installment_count=1, installment_amount=Decimal("1.00"), is_active=True,
    )
    response = client.post(checkout_url(plan), data=valid_payload(plan, plan_id=other_plan.id))
    assert response.status_code == 200  # form re-rendered, not processed
    assert not Order.objects.exists()


# --- Customer resolution ---

def test_existing_contact_is_reused(client, plan, mock_tara_success):
    existing = Contact.objects.create(email="buyer@example.com", first_name="Original", last_name="Name")
    client.post(checkout_url(plan), data=valid_payload(plan, first_name="Different", last_name="Value"))

    assert Contact.objects.count() == 1
    existing.refresh_from_db()
    assert existing.first_name == "Original"  # not overwritten by new checkout submission
    order = Order.objects.get()
    assert order.customer_id == existing.id


def test_email_is_normalized(client, plan, mock_tara_success):
    client.post(checkout_url(plan), data=valid_payload(plan, email="Buyer@EXAMPLE.com"))
    assert Contact.objects.filter(email="buyer@example.com").exists()


def test_phone_is_normalized(client, plan, mock_tara_success):
    client.post(checkout_url(plan), data=valid_payload(plan, phone="+237 600 00 00 00"))
    contact = Contact.objects.get()
    assert contact.phone == "+237600000000"


def test_duplicate_contact_not_created_on_repeat_checkout(client, plan, mock_tara_success):
    client.post(checkout_url(plan), data=valid_payload(plan, idempotency_key="idem-a"))
    client.post(checkout_url(plan), data=valid_payload(plan, idempotency_key="idem-b"))
    assert Contact.objects.filter(email="buyer@example.com").count() == 1


# --- Idempotency (HTTP level) ---

def test_duplicate_submit_same_key_returns_same_order(client, plan, mock_tara_success):
    key = str(uuid.uuid4())
    client.post(checkout_url(plan), data=valid_payload(plan, idempotency_key=key))
    client.post(checkout_url(plan), data=valid_payload(plan, idempotency_key=key))
    assert Order.objects.count() == 1


def test_duplicate_submit_same_key_returns_same_payment_attempt(client, plan, mock_tara_success):
    key = str(uuid.uuid4())
    client.post(checkout_url(plan), data=valid_payload(plan, idempotency_key=key))
    client.post(checkout_url(plan), data=valid_payload(plan, idempotency_key=key))
    assert PaymentAttempt.objects.count() == 1


def test_tara_called_once_after_success_even_on_resubmit(client, plan, mock_tara_success):
    key = str(uuid.uuid4())
    client.post(checkout_url(plan), data=valid_payload(plan, idempotency_key=key))
    client.post(checkout_url(plan), data=valid_payload(plan, idempotency_key=key))
    mock_tara_success.create_payment_link.assert_called_once()


# --- Response behavior ---

def test_successful_checkout_redirects_to_tara_general_link(client, plan, mock_tara_success):
    response = client.post(checkout_url(plan), data=valid_payload(plan))
    assert response.status_code == 302
    assert response.url == "https://taramoney.com/pay/abc123"


def test_no_secrets_in_rendered_checkout_form_html(client, plan):
    response = client.get(checkout_url(plan))
    assert response.status_code == 200
    content = response.content.decode().lower()
    assert "api_key" not in content
    assert "apikey" not in content
    assert "webhook_secret" not in content
    assert "businessid" not in content
