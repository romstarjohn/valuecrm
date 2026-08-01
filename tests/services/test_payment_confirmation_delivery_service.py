import smtplib
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core import mail
from django.utils import timezone

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import Installment, PaymentAttempt, PaymentConfirmation, PaymentPlan
from apps.payments.services import (
    OrderService,
    PaymentAttemptService,
    PaymentConfirmationDeliveryService,
    PaymentConfirmationService,
    PaymentCreditService,
)
from shared.constants import PAYMENT_CONFIRMATION_MAX_ATTEMPTS

pytestmark = pytest.mark.django_db


def make_order(installment_count=1, installment_amount=Decimal("100000.00"), email="buyer@example.com"):
    course = Course.objects.create(cf_course_id=f"crs_{email}", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code=f"plan_{email}", name="Bootcamp", course=course,
        installment_count=installment_count, installment_amount=installment_amount, is_active=True,
    )
    contact = Contact.objects.create(email=email, first_name="Ada", last_name="Lovelace")
    order, _ = OrderService().create_order(contact, plan.id, f"idem-{email}")
    return order


def create_payable_attempt(installment: Installment) -> PaymentAttempt:
    attempt = PaymentAttemptService().create_attempt(installment)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    return attempt


def make_confirmation(email="buyer@example.com", installment_count=1, installment_amount=Decimal("100000.00")):
    order = make_order(installment_count=installment_count, installment_amount=installment_amount, email=email)
    installment = order.installments.order_by("sequence").first()
    attempt = create_payable_attempt(installment)
    PaymentCreditService().apply_verified_success(attempt.id)
    installment.refresh_from_db()
    order.refresh_from_db()
    return PaymentConfirmationService().create_confirmation(attempt, installment, order)


def test_process_pending_sends_email_and_marks_sent():
    confirmation = make_confirmation(email="send-success@example.com")

    processed = PaymentConfirmationDeliveryService().process_pending()

    confirmation.refresh_from_db()
    assert processed == 1
    assert confirmation.status == PaymentConfirmation.Status.SENT
    assert confirmation.sent_at is not None
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["send-success@example.com"]


def test_sent_email_contains_expected_safe_content_and_no_secrets():
    confirmation = make_confirmation(email="content-check@example.com")

    PaymentConfirmationDeliveryService().process_pending()

    body = mail.outbox[0].body
    order = confirmation.order
    assert "Ada" in body
    assert str(order.reference) in body
    assert order.course_name in body
    assert "now accessible" in body  # single-installment FULL_PAYMENT order is eligible after this payment
    for forbidden in ("api_key", "webhook_secret", "Bearer", "tara_product_id", "raw_payload"):
        assert forbidden not in body
    assert "http://" not in body and "https://" not in body  # no payment link/URL ever included


def test_missing_recipient_goes_to_manual_review_without_raising():
    confirmation = make_confirmation(email="will-be-blanked@example.com")
    confirmation.recipient_snapshot = ""
    confirmation.save(update_fields=["recipient_snapshot"])

    PaymentConfirmationDeliveryService().process_pending()

    confirmation.refresh_from_db()
    assert confirmation.status == PaymentConfirmation.Status.MANUAL_REVIEW
    assert confirmation.last_failure_category == PaymentConfirmation.FailureCategory.INVALID_RECIPIENT
    assert len(mail.outbox) == 0


def test_transient_smtp_error_is_retryable_with_backoff():
    confirmation = make_confirmation(email="transient-error@example.com")

    with patch("apps.payments.services.send_mail", side_effect=smtplib.SMTPConnectError(421, "busy")):
        PaymentConfirmationDeliveryService().process_pending()

    confirmation.refresh_from_db()
    assert confirmation.status == PaymentConfirmation.Status.FAILED
    assert confirmation.last_failure_category == PaymentConfirmation.FailureCategory.TRANSIENT_DELIVERY_ERROR
    assert confirmation.attempt_count == 1
    assert confirmation.next_attempt_at is not None
    assert confirmation.next_attempt_at > timezone.now()


def test_bounded_attempts_move_to_manual_review():
    confirmation = make_confirmation(email="bounded-attempts@example.com")

    with patch("apps.payments.services.send_mail", side_effect=smtplib.SMTPConnectError(421, "busy")):
        for _ in range(PAYMENT_CONFIRMATION_MAX_ATTEMPTS):
            confirmation.next_attempt_at = None
            confirmation.save(update_fields=["next_attempt_at"])
            PaymentConfirmationDeliveryService().process_pending()

    confirmation.refresh_from_db()
    assert confirmation.status == PaymentConfirmation.Status.MANUAL_REVIEW
    assert confirmation.last_failure_category == PaymentConfirmation.FailureCategory.MAX_ATTEMPTS_EXCEEDED


def test_unexpected_exception_goes_to_manual_review_not_retried():
    confirmation = make_confirmation(email="unexpected-error@example.com")

    with patch("apps.payments.services.render_to_string", side_effect=ValueError("template broke")):
        PaymentConfirmationDeliveryService().process_pending()

    confirmation.refresh_from_db()
    assert confirmation.status == PaymentConfirmation.Status.MANUAL_REVIEW
    assert confirmation.last_failure_category == PaymentConfirmation.FailureCategory.CONFIGURATION_ERROR


def test_email_delivery_failure_never_changes_payment_status():
    order = make_order(email="delivery-failure-payment-safe@example.com")
    installment = order.installments.get()
    attempt = create_payable_attempt(installment)
    PaymentCreditService().apply_verified_success(attempt.id)
    installment.refresh_from_db()

    with patch("apps.payments.services.send_mail", side_effect=smtplib.SMTPException("boom")):
        PaymentConfirmationDeliveryService().process_pending()

    attempt.refresh_from_db()
    installment.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.SUCCEEDED
    assert installment.status == Installment.Status.PAID


def test_already_sent_confirmation_is_not_resent():
    confirmation = make_confirmation(email="already-sent@example.com")
    PaymentConfirmationDeliveryService().process_pending()
    assert len(mail.outbox) == 1

    PaymentConfirmationDeliveryService().process_pending()  # nothing left to claim

    assert len(mail.outbox) == 1


def test_manual_review_confirmation_is_never_auto_retried():
    confirmation = make_confirmation(email="manual-review-skip@example.com")
    confirmation.status = PaymentConfirmation.Status.MANUAL_REVIEW
    confirmation.save(update_fields=["status"])

    PaymentConfirmationDeliveryService().process_pending()

    assert len(mail.outbox) == 0
    confirmation.refresh_from_db()
    assert confirmation.status == PaymentConfirmation.Status.MANUAL_REVIEW
