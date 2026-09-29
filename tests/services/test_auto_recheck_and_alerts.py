import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock

import pytest
from django.contrib.auth.models import Permission, User
from django.core import mail
from django.test import Client
from django.utils import timezone

from apps.configuration.models import NotificationSettings, StaffAlert
from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.operations.alerts import collect_alert_items, send_staff_alerts
from apps.operations.presentation import sale_state
from apps.payments.models import Order, PaymentAttempt, PaymentPlan, TaraConfig, TaraWebhookEvent
from apps.payments.reconciliation_services import ReconciliationService
from apps.payments.services import OrderService, PaymentAttemptService, PaymentCreditService
from integrations.payments.tara.exceptions import TaraMalformedResponseError
from integrations.payments.tara.schemas import TaraPaymentStatusResponse
from shared.security import encrypt_value

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def tara_config():
    TaraConfig.objects.create(name="Main", business_id="biz_123", is_active=True,
                              api_key=encrypt_value("k"), webhook_secret=encrypt_value("s"))


def reported_sale(email="sarah@example.com", minutes_ago=90, attempt_status=PaymentAttempt.Status.LINK_CREATED, key="k1"):
    course = Course.objects.get_or_create(cf_course_id="crs", defaults={"name": "Locks", "workspace_id": "w"})[0]
    plan = PaymentPlan.objects.get_or_create(code="p1", defaults=dict(
        name="Unique", course=course, installment_count=1, installment_amount=Decimal("1000.00"), is_active=True))[0]
    contact = Contact.objects.create(email=email, first_name="Sarah")
    order, _ = OrderService().create_order(contact, plan.id, key)
    attempt = PaymentAttemptService().create_attempt(order.installments.get())
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    if attempt_status == PaymentAttempt.Status.FAILED:
        PaymentAttemptService().transition(attempt, PaymentAttempt.Status.FAILED)
    event = TaraWebhookEvent.objects.create(
        dedup_key=f"d-{key}", tara_product_id=attempt.tara_product_id, tara_payment_id=f"pay-{key}",
        raw_provider_status="SUCCESS", payment_attempt=attempt,
        processing_status=TaraWebhookEvent.ProcessingStatus.FAILED,
        failure_category=TaraWebhookEvent.FailureCategory.PROVIDER_LOOKUP_INDETERMINATE,
    )
    TaraWebhookEvent.objects.filter(pk=event.pk).update(received_at=timezone.now() - timedelta(minutes=minutes_ago))
    PaymentAttempt.objects.filter(pk=attempt.pk).update(updated_at=timezone.now() - timedelta(minutes=minutes_ago))
    attempt.refresh_from_db()
    return order, attempt


def tara_confirms(mocker, attempt, payment_id):
    client = Mock(business_id="biz_123")
    client.check_payment_status.return_value = TaraPaymentStatusResponse.model_validate({
        "status": "SUCCESS", "payload": json.dumps({
            "businessId": "biz_123", "paymentId": payment_id, "amount": "1000",
            "status": "SUCCESS", "productId": attempt.tara_product_id,
        }),
    })
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=client)
    return client


def recheck():
    counters = {k: 0 for k in ["attempts_examined", "status_checks", "verified_successes", "verified_failures",
                                "still_pending_or_unknown", "safe_error_count"]}
    ReconciliationService()._verify_stale_attempts(counters)
    return counters


# --- 1. Automatic re-check of reported-but-unconfirmed payments ---

@pytest.mark.parametrize("status", [PaymentAttempt.Status.LINK_CREATED, PaymentAttempt.Status.FAILED])
def test_hourly_recheck_credits_payment_tara_reported(mocker, status):
    """Exactly the August production case — now fixed without a click."""
    order, attempt = reported_sale(attempt_status=status)
    tara_confirms(mocker, attempt, "pay-k1")

    counters = recheck()

    attempt.refresh_from_db()
    order.refresh_from_db()
    assert counters["verified_successes"] == 1
    assert attempt.status == PaymentAttempt.Status.SUCCEEDED and order.status == Order.Status.COMPLETED
    event = TaraWebhookEvent.objects.get(tara_product_id=attempt.tara_product_id)
    assert event.processing_status == TaraWebhookEvent.ProcessingStatus.PROCESSED  # leaves "À traiter"
    assert sale_state(order).key == "paid"


def test_recent_report_waits_for_the_normal_delay(mocker):
    reported_sale(minutes_ago=5)
    client = tara_confirms(mocker, Mock(tara_product_id="x"), "pay-k1")
    recheck()
    client.check_payment_status.assert_not_called()


def test_unverifiable_report_backs_off_and_stops_after_max_checks(mocker, settings):
    _, attempt = reported_sale()
    client = Mock(business_id="biz_123")
    client.check_payment_status.return_value = TaraPaymentStatusResponse.model_validate({"status": "ERROR", "message": "NOT_FOUND"})
    client.check_transaction_status.side_effect = TaraMalformedResponseError("not found")
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=client)

    recheck()
    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.LINK_CREATED
    assert attempt.reconciliation_check_count == 1 and attempt.next_reconciliation_check_at > timezone.now()

    calls = client.check_payment_status.call_count
    recheck()  # still inside backoff window
    assert client.check_payment_status.call_count == calls


# --- 2. Team alerts ---

def test_default_alert_address_is_set():
    assert NotificationSettings.current().alert_email == "contact@digital-mind.tech"


def test_alert_digest_sent_once_for_unresolved_report():
    order, _ = reported_sale()

    sent = send_staff_alerts()
    send_staff_alerts()  # next hourly run

    assert len(sent) == 1 and len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == ["contact@digital-mind.tech"]
    assert "1 élément à traiter" in message.subject
    assert "Paiement à vérifier" in message.body and f"/operations/orders/{order.reference}/" in message.body
    assert StaffAlert.objects.filter(key=f"verify:{order.pk}").exists()


def test_no_alert_before_automatic_recheck_had_its_chance():
    reported_sale(minutes_ago=20)
    assert collect_alert_items() == []


def test_no_alert_for_normal_paid_sale(mocker):
    _, attempt = reported_sale()
    PaymentCreditService().apply_verified_success(attempt.id)
    TaraWebhookEvent.objects.update(processing_status=TaraWebhookEvent.ProcessingStatus.PROCESSED)
    assert send_staff_alerts() == [] and mail.outbox == []


def test_disabled_or_empty_address_sends_nothing_and_keeps_items_for_later():
    reported_sale()
    config = NotificationSettings.current()
    config.alerts_enabled = False
    config.save()

    send_staff_alerts()

    assert mail.outbox == [] and not StaffAlert.objects.exists()


def test_send_failure_is_retried_next_run(mocker):
    reported_sale()
    mocker.patch("apps.operations.alerts.send_mail", side_effect=ConnectionError("smtp down"))
    send_staff_alerts()
    assert not StaffAlert.objects.exists()


# --- Settings page ---

def test_notifications_page_requires_permission_and_saves():
    staff = User.objects.create_user("staff", password="x", is_staff=True)
    client = Client()
    client.force_login(staff)
    assert client.get("/settings/notifications/").status_code == 403

    staff.user_permissions.add(Permission.objects.get(codename="change_notificationsettings"))
    client.force_login(User.objects.get(pk=staff.pk))
    page = client.get("/settings/notifications/").content.decode()
    assert "contact@digital-mind.tech" in page

    client.post("/settings/notifications/", {"alert_email": "equipe@example.com", "alerts_enabled": "on"})
    assert NotificationSettings.current().alert_email == "equipe@example.com"

    client.post("/settings/notifications/", {"action": "test"})
    assert mail.outbox[-1].to == ["equipe@example.com"]


def test_notifications_page_warns_when_server_cannot_send_email(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"  # the current prod default
    client = Client()
    client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))

    assert "Aucun e-mail ne part pour l'instant" in client.get("/settings/notifications/").content.decode()
