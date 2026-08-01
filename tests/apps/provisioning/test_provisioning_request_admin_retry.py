from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission, User
from django.test import Client
from django.urls import reverse

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import AdminAuditLog, PaymentAttempt, PaymentPlan
from apps.payments.services import OrderService, PaymentAttemptService, PaymentCreditService
from apps.provisioning.models import ProvisioningRequest

pytestmark = pytest.mark.django_db

CHANGELIST_URL = reverse("admin:provisioning_provisioningrequest_changelist")


@pytest.fixture
def failed_request(db):
    course = Course.objects.create(cf_course_id="crs_retry_admin", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code="retry-admin-plan", name="Bootcamp", course=course,
        installment_count=1, installment_amount=Decimal("100000.00"), is_active=True,
    )
    contact = Contact.objects.create(email="retry-admin@example.com")
    order, _ = OrderService().create_order(contact, plan.id, "idem-retry-admin")
    installment = order.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    attempt = PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentCreditService().apply_verified_success(attempt.id)
    req = ProvisioningRequest.objects.get(order=order)
    req.status = ProvisioningRequest.Status.FAILED
    req.save()
    return req


@pytest.fixture
def superuser_client(db):
    User.objects.create_superuser(username="provisioningadmin", email="pa@example.com", password="x")
    client = Client()
    client.login(username="provisioningadmin", password="x")
    return client


def test_retry_requires_permission(failed_request):
    user = User.objects.create_user(username="staffprovisioning", password="x", is_staff=True)
    user.user_permissions.add(Permission.objects.get(codename="view_provisioningrequest"))
    client = Client()
    client.login(username="staffprovisioning", password="x")

    resp = client.post(CHANGELIST_URL, {
        "action": "retry_action", "_selected_action": [str(failed_request.pk)],
        "confirm_apply": "1", "reason": "trying",
    })
    assert resp.status_code == 403
    failed_request.refresh_from_db()
    assert failed_request.status == ProvisioningRequest.Status.FAILED


def test_retry_via_admin_resets_without_calling_clickfunnels(superuser_client, failed_request, mocker):
    mock_execute = mocker.patch("apps.provisioning.services.ProvisioningService.execute")

    resp = superuser_client.post(CHANGELIST_URL, {
        "action": "retry_action", "_selected_action": [str(failed_request.pk)],
        "confirm_apply": "1", "reason": "operator fixed the issue",
    }, follow=True)
    assert resp.status_code == 200

    failed_request.refresh_from_db()
    assert failed_request.status == ProvisioningRequest.Status.PENDING
    mock_execute.assert_not_called()
    log = AdminAuditLog.objects.get(action_type=AdminAuditLog.ActionType.RETRY_PROVISIONING)
    assert log.reason == "operator fixed the issue"


def test_completed_request_cannot_be_retried_via_admin(superuser_client, failed_request):
    failed_request.status = ProvisioningRequest.Status.COMPLETED
    failed_request.save()

    resp = superuser_client.post(CHANGELIST_URL, {
        "action": "retry_action", "_selected_action": [str(failed_request.pk)],
        "confirm_apply": "1", "reason": "should be rejected",
    }, follow=True)
    assert resp.status_code == 200
    failed_request.refresh_from_db()
    assert failed_request.status == ProvisioningRequest.Status.COMPLETED
