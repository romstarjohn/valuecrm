"""
Concurrent webhook/payment-credit scenarios tested against real Postgres
connections (not SQLite) — mirrors tests/services/test_order_service_concurrency.py
and tests/services/test_checkout_concurrency.py's approach: transaction=True
so separate threads see real, committed state.
"""
import json
import threading
from decimal import Decimal
from unittest.mock import Mock

import pytest
from django.core.cache import cache
from django.db import connection

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import Installment, PaymentAttempt, PaymentPlan, TaraConfig, TaraWebhookEvent
from apps.payments.services import (
    OrderService,
    PaymentAttemptService,
    PaymentCreditService,
    WebhookProcessingService,
)
from integrations.payments.tara.schemas import TaraTransactionStatusResponse
from shared.security import encrypt_value


def _run_concurrently(worker, thread_count=8):
    barrier = threading.Barrier(thread_count)
    errors = []
    lock = threading.Lock()

    def wrapped():
        try:
            barrier.wait(timeout=5)
            worker()
        except Exception as exc:
            with lock:
                errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=wrapped) for _ in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    return errors


@pytest.mark.django_db(transaction=True)
def test_concurrent_duplicate_webhook_delivery_is_deduplicated_safely(mocker):
    cache.clear()
    TaraConfig.objects.create(
        name="Main", business_id="biz_concurrent", is_active=True,
        api_key=encrypt_value("key"), webhook_secret=encrypt_value("secret"),
    )
    course = Course.objects.create(cf_course_id="crs_webhook_concurrent", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code="webhook-concurrent-plan", name="Bootcamp", course=course,
        installment_count=1, installment_amount=Decimal("100000.00"), is_active=True,
    )
    contact = Contact.objects.create(email="webhook-concurrent@example.com")
    order, _ = OrderService().create_order(contact, plan.id, "idem-webhook-concurrent")
    installment = order.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)

    def fake_get_client():
        mock_client = Mock()
        mock_client.check_transaction_status.return_value = TaraTransactionStatusResponse.model_validate({
            "productId": attempt.tara_product_id, "status": "SUCCESS", "message": "ok",
        })
        return mock_client

    mocker.patch("apps.payments.services.TaraConfigService.get_client", side_effect=fake_get_client)

    body = json.dumps({"businessId": "biz_concurrent", "productId": attempt.tara_product_id, "status": "SUCCESS"}).encode()

    def worker():
        WebhookProcessingService().process_webhook(body)

    errors = _run_concurrently(worker, thread_count=8)

    assert not errors, f"Unexpected errors: {errors}"
    assert TaraWebhookEvent.objects.count() == 1  # identical deliveries deduplicated to one event

    attempt.refresh_from_db()
    installment.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.SUCCEEDED
    assert installment.status == Installment.Status.PAID
    assert installment.paid_amount == installment.expected_amount  # credited exactly once, not accumulated


@pytest.mark.django_db(transaction=True)
def test_concurrent_duplicate_verified_success_credits_exactly_once():
    course = Course.objects.create(cf_course_id="crs_credit_concurrent", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code="credit-concurrent-plan", name="Bootcamp", course=course,
        installment_count=1, installment_amount=Decimal("100000.00"), is_active=True,
    )
    contact = Contact.objects.create(email="credit-concurrent@example.com")
    order, _ = OrderService().create_order(contact, plan.id, "idem-credit-concurrent")
    installment = order.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)

    def worker():
        PaymentCreditService().apply_verified_success(attempt.id)

    errors = _run_concurrently(worker, thread_count=8)

    assert not errors, f"Unexpected errors: {errors}"
    installment.refresh_from_db()
    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.SUCCEEDED
    assert installment.status == Installment.Status.PAID
    assert installment.paid_amount == installment.expected_amount


@pytest.mark.django_db(transaction=True)
def test_concurrent_conflicting_tara_payment_id_enters_review_and_credits_neither_incorrectly():
    course_a = Course.objects.create(cf_course_id="crs_conflict_a", name="Bootcamp A", workspace_id="ws_1")
    course_b = Course.objects.create(cf_course_id="crs_conflict_b", name="Bootcamp B", workspace_id="ws_1")
    plan_a = PaymentPlan.objects.create(
        code="conflict-plan-a", name="Bootcamp A", course=course_a,
        installment_count=1, installment_amount=Decimal("100000.00"), is_active=True,
    )
    plan_b = PaymentPlan.objects.create(
        code="conflict-plan-b", name="Bootcamp B", course=course_b,
        installment_count=1, installment_amount=Decimal("100000.00"), is_active=True,
    )
    contact_a = Contact.objects.create(email="conflict-a@example.com")
    contact_b = Contact.objects.create(email="conflict-b@example.com")
    order_a, _ = OrderService().create_order(contact_a, plan_a.id, "idem-conflict-a")
    order_b, _ = OrderService().create_order(contact_b, plan_b.id, "idem-conflict-b")
    attempt_a = PaymentAttemptService().create_attempt(order_a.installments.get())
    attempt_b = PaymentAttemptService().create_attempt(order_b.installments.get())
    PaymentAttemptService().transition(attempt_a, PaymentAttempt.Status.LINK_CREATED)
    PaymentAttemptService().transition(attempt_b, PaymentAttempt.Status.LINK_CREATED)

    shared_payment_id = "raced-shared-payment-id"
    errors = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def credit(attempt_id):
        try:
            barrier.wait(timeout=5)
            PaymentCreditService().apply_verified_success(attempt_id, tara_payment_id=shared_payment_id)
        except Exception as exc:
            with lock:
                errors.append(exc)
        finally:
            connection.close()

    t1 = threading.Thread(target=credit, args=(attempt_a.id,))
    t2 = threading.Thread(target=credit, args=(attempt_b.id,))
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    # Exactly one of the two must have succeeded; the other must have hit the conflict.
    assert len(errors) == 1, f"Expected exactly one PaymentIdConflictError, got: {errors}"

    attempt_a.refresh_from_db()
    attempt_b.refresh_from_db()
    succeeded = [a for a in (attempt_a, attempt_b) if a.status == PaymentAttempt.Status.SUCCEEDED]
    assert len(succeeded) == 1  # exactly one credited, never both
    assert PaymentAttempt.objects.filter(tara_payment_id=shared_payment_id).count() == 1
