from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import (
    AdminAuditLog,
    PaymentAttempt,
    PaymentConfirmation,
    PaymentPlan,
    ReconciliationRun,
)
from apps.payments.admin_services import AdminAuditService
from apps.payments.services import OrderService, PaymentAttemptService, PaymentCreditService
from apps.provisioning.models import ProvisioningRequest

pytestmark = pytest.mark.django_db


def make_full_order(email):
    course = Course.objects.create(cf_course_id=f"crs_{email}", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code=f"plan_{email}", name="Bootcamp", course=course,
        installment_count=1, installment_amount=Decimal("100000.00"), is_active=True,
    )
    contact = Contact.objects.create(email=email)
    order, _ = OrderService().create_order(contact, plan.id, f"idem-{email}")
    installment = order.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    attempt = PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentCreditService().apply_verified_success(attempt.id)
    return order


LIST_ROUTES = {
    "installment_list": "payments.view_installment",
    "payment_attempt_list": "payments.view_paymentattempt",
    "webhook_event_list": "payments.view_tarawebhookevent",
    "confirmation_list": "payments.view_paymentconfirmation",
    "provisioning_list": "provisioning.view_provisioningrequest",
    "reconciliation_list": "payments.view_reconciliationrun",
    "audit_list": "payments.view_adminauditlog",
}


@pytest.mark.parametrize("route_name", LIST_ROUTES.keys())
def test_anonymous_redirected(client, route_name):
    response = client.get(reverse(f"operations:{route_name}"))
    assert response.status_code == 302


@pytest.mark.parametrize("route_name,permission", LIST_ROUTES.items())
def test_staff_without_permission_forbidden(route_name, permission):
    user = User.objects.create_user(username=f"noperm-{route_name}", password="x", is_staff=True)
    client = Client()
    client.login(username=user.username, password="x")
    response = client.get(reverse(f"operations:{route_name}"))
    assert response.status_code == 403


@pytest.mark.parametrize("route_name", LIST_ROUTES.keys())
def test_authorized_access(ops_client, route_name):
    make_full_order(f"list-{route_name}@example.com")
    response = ops_client.get(reverse(f"operations:{route_name}"))
    assert response.status_code == 200


def test_installment_list_filters_by_status(ops_client):
    order = make_full_order("installment-filter@example.com")
    response = ops_client.get(reverse("operations:installment_list"), {"status": "PAID"})
    assert response.status_code == 200
    results = list(response.context["page_obj"])
    assert any(i.order_id == order.pk for i in results)


def test_payment_attempt_list_filters_pending_unknown(ops_client):
    order = make_full_order("attempt-filter@example.com")
    response = ops_client.get(reverse("operations:payment_attempt_list"), {"pending_unknown": "1"})
    assert response.status_code == 200
    for attempt in response.context["page_obj"]:
        assert attempt.status in (PaymentAttempt.Status.PENDING, PaymentAttempt.Status.UNKNOWN)


def test_payment_attempt_list_never_exposes_payment_links(ops_client):
    order = make_full_order("attempt-no-link@example.com")
    installment = order.installments.get()
    attempt = installment.payment_attempts.get()
    attempt.general_link = "https://taramoney.com/pay/secret-token-value"
    attempt.save()
    response = ops_client.get(reverse("operations:payment_attempt_list"))
    assert "secret-token-value" not in response.content.decode()


def test_confirmation_list_filters_by_status(ops_client):
    make_full_order("confirmation-filter@example.com")
    response = ops_client.get(reverse("operations:confirmation_list"), {"status": "PENDING"})
    assert response.status_code == 200
    for c in response.context["page_obj"]:
        assert c.status == PaymentConfirmation.Status.PENDING


def test_provisioning_list_filters_by_status(ops_client):
    make_full_order("provisioning-filter@example.com")
    response = ops_client.get(reverse("operations:provisioning_list"), {"status": "PENDING"})
    assert response.status_code == 200
    for r in response.context["page_obj"]:
        assert r.status == ProvisioningRequest.Status.PENDING


def test_reconciliation_list_filters_by_status(ops_client):
    ReconciliationRun.objects.create(started_at="2026-01-01T00:00:00Z", run_status=ReconciliationRun.RunStatus.FAILED)
    ReconciliationRun.objects.create(started_at="2026-01-01T00:00:00Z", run_status=ReconciliationRun.RunStatus.SUCCESS)
    response = ops_client.get(reverse("operations:reconciliation_list"), {"status": "FAILED"})
    assert response.status_code == 200
    results = list(response.context["page_obj"])
    assert all(r.run_status == "FAILED" for r in results)
    assert len(results) == 1


def test_reconciliation_list_no_secrets(ops_client):
    ReconciliationRun.objects.create(started_at="2026-01-01T00:00:00Z", run_status=ReconciliationRun.RunStatus.SUCCESS)
    response = ops_client.get(reverse("operations:reconciliation_list"))
    content = response.content.decode().lower()
    assert "api_key" not in content
    assert "webhook_secret" not in content
    assert "raw_response" not in content
    assert "raw_payload" not in content


def test_audit_list_shows_safe_fields_only(ops_client):
    order = make_full_order("audit-filter@example.com")
    AdminAuditService().record(
        administrator=None, action_type=AdminAuditLog.ActionType.CANCEL_ORDER,
        target_type=AdminAuditLog.TargetType.ORDER, target_reference=str(order.reference),
        order=order, reason="test reason for audit list", outcome_category=AdminAuditLog.OutcomeCategory.SUCCESS,
    )
    response = ops_client.get(reverse("operations:audit_list"))
    assert response.status_code == 200
    assert "test reason for audit list" in response.content.decode()


def test_audit_list_filters_by_action_type(ops_client):
    order = make_full_order("audit-action-filter@example.com")
    AdminAuditService().record(
        administrator=None, action_type=AdminAuditLog.ActionType.CANCEL_ORDER,
        target_type=AdminAuditLog.TargetType.ORDER, target_reference=str(order.reference),
        order=order, reason="cancel reason", outcome_category=AdminAuditLog.OutcomeCategory.SUCCESS,
    )
    AdminAuditService().record(
        administrator=None, action_type=AdminAuditLog.ActionType.APPLY_MANUAL_DISPOSITION,
        target_type=AdminAuditLog.TargetType.ORDER, target_reference=str(order.reference),
        order=order, reason="disposition reason", outcome_category=AdminAuditLog.OutcomeCategory.SUCCESS,
    )
    response = ops_client.get(reverse("operations:audit_list"), {"action_type": "CANCEL_ORDER"})
    results = list(response.context["page_obj"])
    assert all(e.action_type == "CANCEL_ORDER" for e in results)


def test_webhook_event_list_no_raw_payload_or_phone(ops_client):
    make_full_order("webhook-safe@example.com")
    response = ops_client.get(reverse("operations:webhook_event_list"))
    content = response.content.decode().lower()
    assert "phonenumber" not in content
    assert "raw_payload" not in content


def test_pagination_bounded_across_all_lists(ops_client):
    for i in range(25):
        make_full_order(f"bound-{i}@example.com")
    for route_name in LIST_ROUTES:
        response = ops_client.get(reverse(f"operations:{route_name}"))
        assert len(response.context["page_obj"]) <= 20, route_name
