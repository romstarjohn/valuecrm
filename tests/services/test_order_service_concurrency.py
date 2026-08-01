"""
Concurrent duplicate-order prevention, tested against a real Postgres
connection per thread (not SQLite) — pytest.mark.django_db(transaction=True)
is required here specifically because the default django_db fixture wraps
each test in a single rolled-back transaction, which would make separate
threads see an inconsistent/uncommitted view of each other's work and
wouldn't exercise the actual unique-constraint race this test is for.
"""
import threading
from decimal import Decimal

import pytest
from django.db import connection

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import Order, PaymentPlan
from apps.payments.services import OrderService


@pytest.mark.django_db(transaction=True)
def test_concurrent_create_order_calls_with_same_key_produce_exactly_one_order():
    course = Course.objects.create(cf_course_id="crs_concurrent", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code="bootcamp-concurrent", name="Bootcamp", course=course, installment_count=1,
        installment_amount=Decimal("100000.00"), is_active=True,
    )
    contact = Contact.objects.create(email="concurrent-buyer@example.com")

    idempotency_key = "idem-concurrent-1"
    thread_count = 8
    barrier = threading.Barrier(thread_count)
    errors = []
    results = []
    lock = threading.Lock()

    def worker():
        try:
            barrier.wait(timeout=5)  # maximize actual overlap on the race window
            order, created = OrderService().create_order(contact, plan.id, idempotency_key)
            with lock:
                results.append((order.id, created))
        except Exception as exc:  # capture for assertion, don't swallow
            with lock:
                errors.append(exc)
        finally:
            connection.close()  # each thread gets its own connection; release it explicitly

    threads = [threading.Thread(target=worker) for _ in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert not errors, f"Unexpected errors from concurrent create_order calls: {errors}"
    assert len(results) == thread_count

    order_ids = {order_id for order_id, _created in results}
    assert len(order_ids) == 1, f"All threads must resolve to the same Order id, got: {order_ids}"

    created_flags = [created for _order_id, created in results]
    assert created_flags.count(True) == 1, "Exactly one thread should have actually created the order"
    assert created_flags.count(False) == thread_count - 1, "The rest must resolve to the existing order"

    assert Order.objects.filter(idempotency_key=idempotency_key).count() == 1
    winning_order = Order.objects.get(idempotency_key=idempotency_key)
    assert winning_order.installments.count() == 1  # no duplicate/orphaned installments from a rolled-back loser
