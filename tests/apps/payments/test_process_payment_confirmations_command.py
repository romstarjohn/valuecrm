from decimal import Decimal

import pytest
from django.core import mail
from django.core.management import call_command

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import PaymentAttempt, PaymentConfirmation, PaymentPlan
from apps.payments.services import OrderService, PaymentAttemptService, PaymentCreditService

pytestmark = pytest.mark.django_db


def test_command_sends_pending_confirmation():
    course = Course.objects.create(cf_course_id="crs_cmd_confirm", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code="cmd-confirm-plan", name="Bootcamp", course=course,
        installment_count=1, installment_amount=Decimal("100000.00"), is_active=True,
    )
    contact = Contact.objects.create(email="cmd-confirm@example.com")
    order, _ = OrderService().create_order(contact, plan.id, "idem-cmd-confirm")
    installment = order.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentCreditService().apply_verified_success(attempt.id)
    confirmation = PaymentConfirmation.objects.get(payment_attempt=attempt)
    assert confirmation.status == PaymentConfirmation.Status.PENDING

    call_command("process_payment_confirmations")

    confirmation.refresh_from_db()
    assert confirmation.status == PaymentConfirmation.Status.SENT
    assert len(mail.outbox) == 1


def test_command_handles_no_pending_confirmations_gracefully():
    call_command("process_payment_confirmations")  # should not raise
