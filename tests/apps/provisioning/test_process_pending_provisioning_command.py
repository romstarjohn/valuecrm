from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from django.core.management import call_command

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.enrollments.models import EnrollmentAttempt
from apps.enrollments.schemas import EnrollmentResultDTO
from apps.payments.models import PaymentAttempt, PaymentPlan
from apps.payments.services import OrderService, PaymentAttemptService, PaymentCreditService
from apps.provisioning.models import ProvisioningRequest

pytestmark = pytest.mark.django_db


def test_command_processes_pending_new_flow_request(mocker):
    course = Course.objects.create(cf_course_id="crs_cmd_provisioning", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code="cmd-provisioning-plan", name="Bootcamp", course=course,
        installment_count=1, installment_amount=Decimal("100000.00"), is_active=True,
    )
    contact = Contact.objects.create(email="cmd-provisioning@example.com")
    order, _ = OrderService().create_order(contact, plan.id, "idem-cmd-provisioning")
    installment = order.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentCreditService().apply_verified_success(attempt.id)
    request = ProvisioningRequest.objects.get(order=order)
    assert request.status == ProvisioningRequest.Status.PENDING

    mock_service = MagicMock()
    mock_service.enroll_contact.return_value = EnrollmentResultDTO(
        contact_email=contact.email, course_id=course.cf_course_id,
        status=EnrollmentAttempt.Status.SUCCESS, enrollment_attempt_id=None,
    )
    mocker.patch(
        "apps.provisioning.services.ProvisioningService._build_enrollment_service",
        return_value=(mock_service, MagicMock(workspace_subdomain="hammer", workspace_id="198218")),
    )

    call_command("process_pending_provisioning")

    request.refresh_from_db()
    assert request.status == ProvisioningRequest.Status.COMPLETED


def test_command_never_touches_manual_review_requests(mocker):
    course = Course.objects.create(cf_course_id="crs_cmd_manual_review", name="Bootcamp", workspace_id="ws_1")
    plan = PaymentPlan.objects.create(
        code="cmd-manual-review-plan", name="Bootcamp", course=course,
        installment_count=1, installment_amount=Decimal("100000.00"), is_active=True,
    )
    contact = Contact.objects.create(email="cmd-manual-review@example.com")
    order, _ = OrderService().create_order(contact, plan.id, "idem-cmd-manual-review")
    installment = order.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentCreditService().apply_verified_success(attempt.id)
    request = ProvisioningRequest.objects.get(order=order)
    request.status = ProvisioningRequest.Status.MANUAL_REVIEW
    request.save(update_fields=["status"])

    mock_execute = mocker.patch("apps.provisioning.services.ProvisioningService.execute")
    call_command("process_pending_provisioning")

    mock_execute.assert_not_called()


def test_command_handles_no_pending_requests_gracefully():
    call_command("process_pending_provisioning")  # should not raise
