from decimal import Decimal

import pytest
from django.urls import reverse

from apps.courses.models import Course
from apps.payments.models import PaymentPlan

pytestmark = pytest.mark.django_db

CHECKOUT_URL = reverse("payments:checkout_start")


@pytest.fixture
def course():
    return Course.objects.create(cf_course_id="crs_1", name="Bootcamp", workspace_id="ws_1")


def test_active_plans_are_displayed(client, course):
    PaymentPlan.objects.create(
        code="active-plan", name="Active Plan", course=course, description="Learn things.",
        installment_count=3, installment_amount=Decimal("34000.00"), is_active=True,
    )
    response = client.get(CHECKOUT_URL)
    assert response.status_code == 200
    assert b"Active Plan" in response.content
    assert b"Learn things." in response.content


def test_inactive_plans_are_hidden(client, course):
    PaymentPlan.objects.create(
        code="inactive-plan", name="Inactive Plan", course=course,
        installment_count=1, installment_amount=Decimal("50000.00"), is_active=False,
    )
    response = client.get(CHECKOUT_URL)
    assert response.status_code == 200
    assert b"Inactive Plan" not in response.content


def test_displayed_price_and_course_are_server_derived(client, course):
    PaymentPlan.objects.create(
        code="derived-plan", name="Derived Plan", course=course,
        installment_count=2, installment_amount=Decimal("34000.00"), currency=PaymentPlan.Currency.XAF, is_active=True,
    )
    response = client.get(CHECKOUT_URL)
    content = response.content.decode()
    assert "68000.00 XAF" in content or "68,000.00 XAF" in content  # computed_total = 2 * 34000
    assert course.name in content


def test_empty_state_shown_when_no_active_plans(client):
    response = client.get(CHECKOUT_URL)
    assert response.status_code == 200
    assert b"No plans available" in response.content


def test_access_policy_displayed_in_customer_readable_language(client, course):
    PaymentPlan.objects.create(
        code="policy-plan", name="Policy Plan", course=course,
        installment_count=1, installment_amount=Decimal("50000.00"),
        access_policy=PaymentPlan.AccessPolicy.FIRST_INSTALLMENT, is_active=True,
    )
    response = client.get(CHECKOUT_URL)
    assert b"After first verified installment" in response.content


def test_checkout_page_uses_public_base_template_not_staff_portal(client, course):
    """The staff sidebar/topbar (Contacts/Courses/Enrollments nav) must never appear on a public checkout page."""
    PaymentPlan.objects.create(
        code="shell-plan", name="Shell Plan", course=course,
        installment_count=1, installment_amount=Decimal("50000.00"), is_active=True,
    )
    response = client.get(CHECKOUT_URL)
    content = response.content.decode()
    assert "sidebar-wrapper" not in content
    assert "Django Admin" not in content


def test_multiple_active_plans_all_rendered_as_selectable_options(client, course):
    """Plans render as radio inputs sharing one name — the browser can select exactly one."""
    PaymentPlan.objects.create(
        code="plan-a", name="Plan A", course=course,
        installment_count=1, installment_amount=Decimal("10000.00"), is_active=True,
    )
    PaymentPlan.objects.create(
        code="plan-b", name="Plan B", course=course,
        installment_count=1, installment_amount=Decimal("20000.00"), is_active=True,
    )
    response = client.get(CHECKOUT_URL)
    content = response.content.decode()
    assert content.count('name="plan_id"') == 2
    assert "Plan A" in content
    assert "Plan B" in content


def test_first_plan_preselected_on_initial_load(client, course):
    plan = PaymentPlan.objects.create(
        code="first-plan", name="First Plan", course=course, display_order=1,
        installment_count=1, installment_amount=Decimal("10000.00"), is_active=True,
    )
    PaymentPlan.objects.create(
        code="second-plan", name="Second Plan", course=course, display_order=2,
        installment_count=1, installment_amount=Decimal("20000.00"), is_active=True,
    )
    response = client.get(CHECKOUT_URL)
    content = response.content.decode()
    marker = f'value="{plan.id}" id="plan-{plan.id}"'
    start = content.index(marker)
    end = content.index(">", start)
    assert "checked" in content[start:end]
