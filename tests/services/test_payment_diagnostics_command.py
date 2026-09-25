from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import PaymentAttempt, PaymentPlan
from apps.payments.services import OrderService, PaymentAttemptService

pytestmark = pytest.mark.django_db


def test_payment_diagnostics_is_read_only_and_hides_personal_data():
    course = Course.objects.create(cf_course_id="crs_diag", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code="diag-1x", name="Bootcamp", course=course,
        installment_count=1, installment_amount=Decimal("1000.00"), is_active=True,
    )
    contact = Contact.objects.create(email="private@example.com", first_name="Secret", last_name="Person")
    order, _ = OrderService().create_order(contact, plan.id, "idem-diag")
    attempt = PaymentAttemptService().create_attempt(order.installments.get())
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)

    out = StringIO()
    call_command("payment_diagnostics", stdout=out)
    text = out.getvalue()

    assert str(order.reference) in text
    assert "status=LINK_CREATED" in text
    assert "(no webhook received)" in text
    assert "private@example.com" not in text and "Secret" not in text
    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.LINK_CREATED


def test_raw_status_masks_values_outside_whitelist(mocker):
    client = mocker.Mock(api_key="k", business_id="b", BASE_URL="https://tara.invalid")
    client._request.return_value = {
        "status": "FAILED", "message": "Transaction not found",
        "data": {"transactionId": "tx-1", "phoneNumber": "690000000"},
    }
    mocker.patch("apps.payments.management.commands.payment_diagnostics.TaraConfigService.get_client", return_value=client)

    out = StringIO()
    call_command("payment_diagnostics", raw_status="vcrm-abc", stdout=out)
    text = out.getvalue()

    assert "status: 'FAILED'" in text
    assert "message: 'Transaction not found'" in text
    assert "phoneNumber: <str, masked>" in text
    assert "690000000" not in text and "tx-1" not in text


def test_paid_list_marks_transactions_matching_webhook_payment_id(mocker):
    from apps.payments.models import TaraWebhookEvent
    from integrations.payments.tara.schemas import TaraTransactionListItem

    TaraWebhookEvent.objects.create(dedup_key="d1", tara_product_id="vcrm-1", tara_payment_id="pay-1", raw_provider_status="SUCCESS")
    client = mocker.Mock()
    client.list_paid_transactions.return_value = [
        TaraTransactionListItem.model_validate({"transactionId": "pay-1", "amount": 1000, "currency": "XAF", "status": "PAID"}),
        TaraTransactionListItem.model_validate({"transactionId": "other", "amount": 500, "currency": "XAF", "status": "PAID"}),
    ]
    mocker.patch("apps.payments.management.commands.payment_diagnostics.TaraConfigService.get_client", return_value=client)

    out = StringIO()
    call_command("payment_diagnostics", paid_list=True, stdout=out)
    text = out.getvalue()

    assert "transactionId=pay-1" in text and "matches webhook for product vcrm-1" in text
    assert text.count("<== matches") == 1


def test_raw_status_expands_json_text_fields(mocker):
    import json
    client = mocker.Mock(api_key="k", business_id="b", BASE_URL="https://tara.invalid")
    client._request.return_value = {
        "status": "SUCCESS",
        "payload": json.dumps({"productId": "vcrm-1", "amount": "1000", "phoneNumber": "690000000"}),
    }
    mocker.patch("apps.payments.management.commands.payment_diagnostics.TaraConfigService.get_client", return_value=client)

    out = StringIO()
    call_command("payment_diagnostics", raw_status="pay-1", stdout=out)
    text = out.getvalue()

    assert "payload (JSON text): {" in text
    assert "productId: 'vcrm-1'" in text and "amount: '1000'" in text
    assert "690000000" not in text
