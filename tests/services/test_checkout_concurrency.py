"""
Concurrent duplicate checkout submission, tested against real Postgres
connections (not SQLite) — mirrors tests/services/test_order_service_concurrency.py's
approach and rationale: pytest.mark.django_db(transaction=True) is required so
separate threads see real, committed state rather than one rolled-back
per-test transaction.
"""
import threading
from decimal import Decimal
from unittest.mock import Mock

import pytest
from django.core.cache import cache
from django.db import connection
from django.test import override_settings

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import Order, PaymentAttempt, PaymentPlan
from apps.payments.services import CheckoutService
from integrations.payments.tara.schemas import TaraPaymentLinkResponse


@pytest.mark.django_db(transaction=True)
def test_concurrent_checkout_submissions_with_same_key_produce_one_order_and_one_attempt(settings, mocker):
    settings.PUBLIC_BASE_URL = "https://checkout.example.com"
    cache.clear()

    course = Course.objects.create(cf_course_id="crs_concurrent_checkout", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code="bootcamp-concurrent-checkout", name="Bootcamp", course=course,
        installment_count=1, installment_amount=Decimal("100000.00"), is_active=True,
    )
    contact = Contact.objects.create(email="concurrent-checkout@example.com")

    def fake_get_client():
        mock_client = Mock()
        mock_client.create_payment_link.return_value = TaraPaymentLinkResponse.model_validate({
            "status": "success", "message": "ok", "generalLink": "https://taramoney.com/pay/concurrent",
        })
        return mock_client

    mocker.patch("apps.payments.services.TaraConfigService.get_client", side_effect=fake_get_client)

    idempotency_key = "idem-concurrent-checkout-1"
    thread_count = 8
    barrier = threading.Barrier(thread_count)
    errors = []
    results = []
    lock = threading.Lock()

    def worker():
        try:
            barrier.wait(timeout=5)
            result = CheckoutService().start_checkout(contact, plan.id, idempotency_key)
            with lock:
                results.append(result)
        except Exception as exc:
            with lock:
                errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert not errors, f"Unexpected errors from concurrent checkout calls: {errors}"
    assert len(results) == thread_count
    assert Order.objects.filter(idempotency_key=idempotency_key).count() == 1
    assert PaymentAttempt.objects.count() == 1

    order = Order.objects.get(idempotency_key=idempotency_key)
    assert order.installments.count() == 1
