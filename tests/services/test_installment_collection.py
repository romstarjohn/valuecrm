"""
Installments after the first (decided 2026-09-25): reminder e-mails if the
customer has an e-mail, access suspended 24 h after the end of the due day,
restored automatically once paid, installment 2+ payable via a personal link.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from django.core import mail
from django.test import Client

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import Installment, InstallmentReminder, Order, PaymentAttempt, PaymentPlan
from apps.payments.services import (
    CheckoutService,
    InstallmentCollectionService,
    OrderService,
    PaymentAttemptService,
    PaymentCreditService,
)
from integrations.payments.tara.schemas import TaraPaymentLinkResponse

pytestmark = pytest.mark.django_db
DOUALA = ZoneInfo("Africa/Douala")
DUE = date(2026, 10, 10)


def at(day, hour=0, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=DOUALA)


@pytest.fixture(autouse=True)
def rules(settings):
    settings.BUSINESS_TIME_ZONE = "Africa/Douala"
    settings.INSTALLMENT_REMINDER_DAYS_BEFORE = 3
    settings.INSTALLMENT_SUSPENSION_GRACE_HOURS = 24


def paid_first_of_three(email="awa@example.com", access_policy=PaymentPlan.AccessPolicy.FIRST_INSTALLMENT):
    course = Course.objects.create(cf_course_id=f"crs_{email}", name="Locks", workspace_id="w")
    plan = PaymentPlan.objects.create(
        code=f"p-{email}", name="3 fois", course=course, installment_count=3,
        installment_amount=Decimal("20000.00"), installment_interval_days=30, is_active=True,
        access_policy=access_policy,
    )
    contact = Contact.objects.create(email=email, first_name="Awa")
    order, _ = OrderService().create_order(contact, plan.id, f"k-{email}", start_date=DUE - timedelta(days=30))
    first = order.installments.get(sequence=1)
    attempt = PaymentAttemptService().create_attempt(first)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentCreditService().apply_verified_success(attempt.id)
    order.refresh_from_db()
    return order


@pytest.fixture
def clickfunnels(mocker):
    """ClickFunnels boundary: access exists; freeze/resume emulate the real order transitions."""
    def freeze(order_id, reason, administrator=None, request=None):
        Order.objects.filter(pk=order_id).update(status=Order.Status.SUSPENDED)

    def resume(order_id, reason, administrator=None, request=None):
        Order.objects.filter(pk=order_id).update(status=Order.Status.ACTIVE)

    base = "apps.payments.admin_services.EnrollmentAdministrationService"
    return {
        "access": mocker.patch(f"{base}.find_open_access", return_value=object()),
        "freeze": mocker.patch(f"{base}.freeze_order_enrollment", side_effect=freeze),
        "resume": mocker.patch(f"{base}.resume_order_enrollment", side_effect=resume),
    }


# --- Reminders ---

def test_reminder_before_due_is_sent_once_with_personal_link():
    order = paid_first_of_three()

    InstallmentCollectionService().run(now=at(DUE - timedelta(days=3), 9))
    InstallmentCollectionService().run(now=at(DUE - timedelta(days=2), 9))  # re-run: no duplicate

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == ["awa@example.com"]
    assert "arrive bientôt" in message.subject
    assert "20000,00 XAF" in message.body and "/paiement/versement/" in message.body
    assert InstallmentReminder.objects.get(installment__order=order).kind == InstallmentReminder.Kind.BEFORE_DUE


def test_due_day_reminder_announces_suspension_time():
    paid_first_of_three()
    InstallmentCollectionService().run(now=at(DUE, 8))

    body = mail.outbox[-1].body
    assert "à payer aujourd'hui" in mail.outbox[-1].subject
    assert "suspendu le 12/10/2026 à 00:00" in body


def test_no_email_means_no_reminder_but_visible_skip():
    order = paid_first_of_three(email="")
    InstallmentCollectionService().run(now=at(DUE - timedelta(days=1), 9))

    assert mail.outbox == []
    reminder = InstallmentReminder.objects.get(installment__order=order)
    assert reminder.status == InstallmentReminder.Status.SKIPPED_NO_EMAIL


def test_failed_email_is_retried_on_next_run(mocker):
    paid_first_of_three()
    mocker.patch("apps.payments.services.send_mail", side_effect=[ConnectionError("smtp down"), 1])

    InstallmentCollectionService().run(now=at(DUE - timedelta(days=3), 9))
    InstallmentCollectionService().run(now=at(DUE - timedelta(days=3), 10))

    reminder = InstallmentReminder.objects.get()
    assert reminder.status == InstallmentReminder.Status.SENT and reminder.attempts == 2


# --- Suspension exactly 24 h after the end of the due day ---

def test_access_not_suspended_before_deadline(clickfunnels):
    order = paid_first_of_three()
    InstallmentCollectionService().run(now=at(DUE + timedelta(days=1), 23, 59))

    clickfunnels["freeze"].assert_not_called()
    order.refresh_from_db()
    assert order.status == Order.Status.PAST_DUE  # late, but still inside the 24 h grace


def test_access_suspended_after_deadline_and_customer_told(clickfunnels):
    order = paid_first_of_three()
    result = InstallmentCollectionService().run(now=at(DUE + timedelta(days=2), 0, 5))

    assert result["accesses_suspended"] == 1
    clickfunnels["freeze"].assert_called_once()
    assert "versement 2/3 impayé" in clickfunnels["freeze"].call_args.args[1]
    order.refresh_from_db()
    assert order.status == Order.Status.SUSPENDED and order.suspended_for_nonpayment_at is not None
    assert "suspendu" in mail.outbox[-1].subject and "/paiement/versement/" in mail.outbox[-1].body

    InstallmentCollectionService().run(now=at(DUE + timedelta(days=2), 1))
    clickfunnels["freeze"].assert_called_once()  # never twice


def test_nothing_to_suspend_when_access_was_never_opened(clickfunnels):
    clickfunnels["access"].return_value = None  # e.g. "une fois tout payé"
    paid_first_of_three(access_policy=PaymentPlan.AccessPolicy.FULL_PAYMENT)

    InstallmentCollectionService().run(now=at(DUE + timedelta(days=5), 9))

    clickfunnels["freeze"].assert_not_called()


# --- Restoration after payment ---

def test_access_restored_automatically_once_installment_paid(clickfunnels):
    order = paid_first_of_three()
    InstallmentCollectionService().run(now=at(DUE + timedelta(days=2), 1))
    second = order.installments.get(sequence=2)
    attempt = PaymentAttemptService().create_attempt(second)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentCreditService().apply_verified_success(attempt.id)

    assert InstallmentCollectionService().resume_if_settled(order.id) is True

    clickfunnels["resume"].assert_called_once()
    order.refresh_from_db()
    assert order.status == Order.Status.ACTIVE and order.suspended_for_nonpayment_at is None


def test_manual_suspension_is_never_lifted_automatically(clickfunnels):
    order = paid_first_of_three()
    Order.objects.filter(pk=order.pk).update(status=Order.Status.SUSPENDED)  # staff decision, no marker

    InstallmentCollectionService().run(now=at(DUE - timedelta(days=10), 9))

    clickfunnels["resume"].assert_not_called()


def test_dry_run_changes_nothing(clickfunnels):
    order = paid_first_of_three()
    result = InstallmentCollectionService().run(now=at(DUE + timedelta(days=3), 9), dry_run=True)

    assert any("suspendre" in line for line in result["planned"])
    assert mail.outbox == []
    clickfunnels["freeze"].assert_not_called()
    order.refresh_from_db()
    assert order.status == Order.Status.ACTIVE


# --- Paying installment 2 with the personal link ---

@pytest.fixture
def tara(mocker):
    client = Mock()
    client.create_payment_link.return_value = TaraPaymentLinkResponse.model_validate(
        {"status": "success", "message": "ok", "generalLink": "https://taramoney.com/pay/v2"},
    )
    mocker.patch("apps.payments.services.TaraConfigService.get_client", return_value=client)
    return client


def test_personal_link_get_only_shows_and_post_pays_installment_2(tara):
    order = paid_first_of_three()
    token = CheckoutService().build_installment_payment_token(order.reference)
    url = f"/paiement/versement/{token}/"

    page = Client().get(url)
    assert page.status_code == 200
    assert "Versement 2 sur 3" in page.content.decode()
    tara.create_payment_link.assert_not_called()  # e-mail scanners pre-open links

    response = Client().post(url)

    assert response.status_code == 302 and response["Location"] == "https://taramoney.com/pay/v2"
    attempt = PaymentAttempt.objects.get(installment__order=order, installment__sequence=2)
    assert attempt.status == PaymentAttempt.Status.LINK_CREATED
    assert "installment 2 of 3" in tara.create_payment_link.call_args.kwargs["product_description"]


def test_invalid_personal_link_is_rejected():
    assert Client().get("/paiement/versement/forged-token/").status_code == 400


def test_status_page_reflects_installment_being_paid(tara):
    order = paid_first_of_three()
    result = CheckoutService().pay_next_installment(order)

    page = Client().get(f"/paiement/status/{result.signed_reference}/").content.decode()

    assert "Poursuivez votre paiement" in page  # installment 2 pending, not "Paiement confirmé" from installment 1


def test_late_sale_catching_up_returns_to_active():
    order = paid_first_of_three()
    InstallmentCollectionService().run(now=at(DUE + timedelta(days=1), 9))
    order.refresh_from_db()
    assert order.status == Order.Status.PAST_DUE

    second = order.installments.get(sequence=2)
    attempt = PaymentAttemptService().create_attempt(second)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    Installment.objects.filter(pk=second.pk).update(due_date=date.today() + timedelta(days=1))  # not overdue any more
    PaymentCreditService().apply_verified_success(attempt.id)

    order.refresh_from_db()
    assert order.status == Order.Status.ACTIVE
