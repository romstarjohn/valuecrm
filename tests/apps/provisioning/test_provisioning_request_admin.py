from decimal import Decimal

import pytest
from django.urls import reverse

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import PaymentAttempt, PaymentPlan
from apps.payments.services import OrderService, PaymentAttemptService, PaymentCreditService
from apps.provisioning.models import ProvisioningRequest

pytestmark = pytest.mark.django_db

CHANGELIST_URL = reverse("admin:provisioning_provisioningrequest_changelist")


@pytest.fixture
def new_flow_request():
    course = Course.objects.create(cf_course_id="crs_admin_provisioning", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code="admin-provisioning-plan", name="Bootcamp", course=course,
        installment_count=1, installment_amount=Decimal("100000.00"), is_active=True,
    )
    contact = Contact.objects.create(email="admin-provisioning@example.com")
    order, _ = OrderService().create_order(contact, plan.id, "idem-admin-provisioning")
    installment = order.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentCreditService().apply_verified_success(attempt.id)
    return ProvisioningRequest.objects.get(order=order)


def test_anonymous_cannot_view(client, new_flow_request):
    response = client.get(CHANGELIST_URL)
    assert response.status_code == 302
    assert "/admin/login/" in response.url


def test_staff_without_permissions_cannot_view(staff_client, new_flow_request):
    response = staff_client.get(CHANGELIST_URL)
    assert response.status_code == 403


def test_superuser_can_view_new_flow_request(superuser_client, new_flow_request):
    response = superuser_client.get(CHANGELIST_URL)
    assert response.status_code == 200
    assert str(new_flow_request.contact.email).encode() in response.content


def test_status_field_is_readonly_and_cannot_be_hand_edited(superuser_client, new_flow_request):
    """
    ProvisioningRequestAdmin (unlike OrderAdmin/PaymentAttemptAdmin) keeps
    add/change/delete permissions at Django's default (superusers may open
    the change form, to use the registered actions), but every work-record
    field — including status — is listed in readonly_fields, so a POST
    attempting to hand-edit status is silently ignored, not honored.
    """
    assert new_flow_request.status == ProvisioningRequest.Status.PENDING
    response = superuser_client.post(
        reverse("admin:provisioning_provisioningrequest_change", args=[new_flow_request.pk]),
        data={"status": ProvisioningRequest.Status.COMPLETED},
    )
    assert response.status_code in (200, 302)
    new_flow_request.refresh_from_db()
    assert new_flow_request.status == ProvisioningRequest.Status.PENDING  # unchanged
