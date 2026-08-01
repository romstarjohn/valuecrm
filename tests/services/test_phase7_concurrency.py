"""
Phase 7 concurrency guarantees against real Postgres connections — mirrors
tests/services/test_webhook_concurrency.py's approach: transaction=True with
real threads so separate connections see real, committed state.
"""
import threading
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from django.db import connection

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.enrollments.models import EnrollmentAttempt
from apps.enrollments.schemas import EnrollmentResultDTO
from apps.payments.models import PaymentAttempt, PaymentConfirmation, PaymentPlan
from apps.payments.services import (
    OrderService,
    PaymentAttemptService,
    PaymentConfirmationDeliveryService,
    PaymentCreditService,
)
from apps.provisioning.models import ProvisioningAttempt, ProvisioningRequest
from apps.provisioning.services import ProvisioningService


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
def test_concurrent_duplicate_verified_success_creates_exactly_one_confirmation_and_one_provisioning_request():
    course = Course.objects.create(cf_course_id="crs_phase7_concurrent", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code="phase7-concurrent-plan", name="Bootcamp", course=course,
        installment_count=1, installment_amount=Decimal("100000.00"), is_active=True,
    )
    contact = Contact.objects.create(email="phase7-concurrent@example.com")
    order, _ = OrderService().create_order(contact, plan.id, "idem-phase7-concurrent")
    installment = order.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)

    def worker():
        PaymentCreditService().apply_verified_success(attempt.id)

    errors = _run_concurrently(worker, thread_count=8)

    assert not errors, f"Unexpected errors: {errors}"
    assert PaymentConfirmation.objects.filter(payment_attempt=attempt).count() == 1
    assert ProvisioningRequest.objects.filter(order=order).count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_create_request_from_order_for_two_different_orders_same_contact_course_dedupes():
    """
    Two different Orders for the same (contact, course) racing to create a
    ProvisioningRequest must never both succeed — the DB unique constraint
    (contact, course) is the authority; the loser's IntegrityError must be
    handled by returning the winner, never raised to the caller.
    """
    course = Course.objects.create(cf_course_id="crs_phase7_dual_order", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code="phase7-dual-order-plan", name="Bootcamp", course=course,
        installment_count=1, installment_amount=Decimal("100000.00"), is_active=True,
    )
    contact = Contact.objects.create(email="phase7-dual-order@example.com")
    order_a, _ = OrderService().create_order(contact, plan.id, "idem-phase7-dual-order-a")
    order_b, _ = OrderService().create_order(contact, plan.id, "idem-phase7-dual-order-b")

    results = []
    lock = threading.Lock()

    def make_worker(order):
        def worker():
            req = ProvisioningService().create_request_from_order(order)
            with lock:
                results.append(req.id)
        return worker

    barrier = threading.Barrier(2)
    errors = []

    def wrapped(order):
        try:
            barrier.wait(timeout=5)
            make_worker(order)()
        except Exception as exc:
            with lock:
                errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=wrapped, args=(o,)) for o in (order_a, order_b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert not errors, f"Unexpected errors: {errors}"
    assert len(set(results)) == 1  # both workers ended up with the SAME request
    assert ProvisioningRequest.objects.filter(contact=contact, course=course).count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_provisioning_execute_claims_only_once(mocker):
    """Two workers racing to process the same PENDING ProvisioningRequest must only have one of them actually call ClickFunnels."""
    course = Course.objects.create(cf_course_id="crs_phase7_claim", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code="phase7-claim-plan", name="Bootcamp", course=course,
        installment_count=1, installment_amount=Decimal("100000.00"), is_active=True,
    )
    contact = Contact.objects.create(email="phase7-claim@example.com")
    order, _ = OrderService().create_order(contact, plan.id, "idem-phase7-claim")
    installment = order.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentCreditService().apply_verified_success(attempt.id)
    request = ProvisioningRequest.objects.get(order=order)

    call_count = {"n": 0}
    call_lock = threading.Lock()

    def fake_enroll_contact(**kwargs):
        with call_lock:
            call_count["n"] += 1
        return EnrollmentResultDTO(
            contact_email=contact.email, course_id=course.cf_course_id,
            status=EnrollmentAttempt.Status.SUCCESS, enrollment_attempt_id=None,
        )

    mock_service = MagicMock()
    mock_service.enroll_contact.side_effect = fake_enroll_contact
    mocker.patch(
        "apps.provisioning.services.ProvisioningService._build_enrollment_service",
        return_value=(mock_service, MagicMock(workspace_subdomain="hammer", workspace_id="198218")),
    )

    def worker():
        ProvisioningService().execute(request)

    errors = _run_concurrently(worker, thread_count=8)

    assert not errors, f"Unexpected errors: {errors}"
    assert call_count["n"] == 1  # only the winning claimant called ClickFunnels
    request.refresh_from_db()
    assert request.status == ProvisioningRequest.Status.COMPLETED
    assert ProvisioningAttempt.objects.filter(provisioning_request=request, status=ProvisioningAttempt.Status.SUCCESS).count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_confirmation_delivery_claims_only_once():
    """Two workers racing to process the same PENDING PaymentConfirmation must only have one of them actually send the email."""
    course = Course.objects.create(cf_course_id="crs_phase7_email_claim", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code="phase7-email-claim-plan", name="Bootcamp", course=course,
        installment_count=1, installment_amount=Decimal("100000.00"), is_active=True,
    )
    contact = Contact.objects.create(email="phase7-email-claim@example.com")
    order, _ = OrderService().create_order(contact, plan.id, "idem-phase7-email-claim")
    installment = order.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentCreditService().apply_verified_success(attempt.id)

    def worker():
        PaymentConfirmationDeliveryService().process_pending()

    errors = _run_concurrently(worker, thread_count=8)

    assert not errors, f"Unexpected errors: {errors}"
    confirmation = PaymentConfirmation.objects.get(payment_attempt=attempt)
    assert confirmation.status == PaymentConfirmation.Status.SENT
    assert confirmation.attempt_count == 1  # claimed and sent exactly once across 8 racing workers
