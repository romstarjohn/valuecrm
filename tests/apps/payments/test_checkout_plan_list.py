from decimal import Decimal

import pytest
from django.urls import reverse

from apps.courses.models import CheckoutOffer, Course
from apps.payments.models import PaymentPlan

pytestmark = pytest.mark.django_db

CHECKOUT_SLUG = "bootcamp"
CHECKOUT_URL = reverse("payments:checkout_start", args=[CHECKOUT_SLUG])


@pytest.fixture
def course():
    return Course.objects.create(cf_course_id="crs_1", name="Bootcamp", workspace_id="ws_1")


@pytest.fixture
def offer(course):
    """The product this page addresses — every test below needs this to exist
    for CHECKOUT_URL to resolve to something other than a 404."""
    return CheckoutOffer.objects.create(
        course=course, slug=CHECKOUT_SLUG, title="Bootcamp",
        mockup_image="images/checkout/monafrolibre-tropical-course.jpg", mockup_alt="Bootcamp mockup",
    )


def test_unknown_product_slug_returns_404(client):
    response = client.get(reverse("payments:checkout_start", args=["does-not-exist"]))
    assert response.status_code == 404


def test_active_plans_are_displayed(client, course, offer):
    PaymentPlan.objects.create(
        code="active-plan", name="Active Plan", course=course, description="Learn things.",
        installment_count=3, installment_amount=Decimal("34000.00"), is_active=True,
    )
    response = client.get(CHECKOUT_URL)
    assert response.status_code == 200
    assert b"En 3 fois" in response.content
    assert b"Learn things." in response.content


def test_inactive_plans_are_hidden(client, course, offer):
    PaymentPlan.objects.create(
        code="inactive-plan", name="Inactive Plan", course=course,
        installment_count=1, installment_amount=Decimal("50000.00"), is_active=False,
    )
    response = client.get(CHECKOUT_URL)
    assert response.status_code == 200
    assert b"Inactive Plan" not in response.content


def test_displayed_price_and_product_title_are_server_derived(client, course, offer):
    PaymentPlan.objects.create(
        code="derived-plan", name="Derived Plan", course=course,
        installment_count=2, installment_amount=Decimal("34000.00"), currency=PaymentPlan.Currency.XAF, is_active=True,
    )
    response = client.get(CHECKOUT_URL)
    content = response.content.decode()
    assert "68\xa0000,00 XAF" in content  # computed_total = 2 * 34000
    assert offer.title in content


def test_empty_state_shown_when_product_has_no_active_plans(client, course, offer):
    """The product exists (its offer resolves), but nothing is currently purchasable."""
    response = client.get(CHECKOUT_URL)
    assert response.status_code == 200
    assert b"Aucun plan disponible" in response.content


def test_access_policy_displayed_in_customer_readable_language(client, course, offer):
    PaymentPlan.objects.create(
        code="policy-plan", name="Policy Plan", course=course,
        installment_count=1, installment_amount=Decimal("50000.00"),
        access_policy=PaymentPlan.AccessPolicy.FIRST_INSTALLMENT, is_active=True,
    )
    response = client.get(CHECKOUT_URL)
    assert "Accès à la formation après confirmation du premier paiement.".encode() in response.content


def test_installment_summary_distinguishes_due_today_from_total(client, course, offer):
    PaymentPlan.objects.create(
        code="split-price", name="Three payments", course=course,
        installment_count=3, installment_amount=Decimal("34000.00"), is_active=True,
    )
    content = client.get(CHECKOUT_URL).content.decode()
    # Server-rendered prices must be correct even before JavaScript runs.
    assert 'id="summary-price">102\xa0000,00 XAF</dd>' in content
    assert 'id="summary-total-due">34\xa0000,00 <span>XAF</span></dd>' in content


def test_invalid_details_preserve_selected_plan_and_summary(client, course, offer):
    PaymentPlan.objects.create(
        code="first", name="First", course=course, display_order=1,
        installment_count=1, installment_amount=Decimal("99000.00"), is_active=True,
    )
    selected = PaymentPlan.objects.create(
        code="selected", name="Selected", course=course, display_order=2,
        installment_count=2, installment_amount=Decimal("25000.00"), is_active=True,
    )
    response = client.post(CHECKOUT_URL, {
        "plan_id": selected.id, "email": "invalid", "first_name": "Ada",
        "idempotency_key": "preserved-checkout",
    })
    content = response.content.decode()
    assert response.context["selected_plan"] == selected
    assert 'id="summary-total-due">25\xa0000,00 <span>XAF</span></dd>' in content
    assert 'value="Ada"' in content
    assert 'value="preserved-checkout"' in content


def test_invalid_plan_does_not_display_a_different_purchase(client, course, offer):
    PaymentPlan.objects.create(
        code="available", name="Available", course=course,
        installment_count=1, installment_amount=Decimal("99000.00"), is_active=True,
    )
    response = client.post(CHECKOUT_URL, {
        "plan_id": "999999", "email": "ada@example.com", "idempotency_key": "invalid-plan",
    })
    assert response.context["selected_plan"] is None
    assert 'id="summary-total-due">—</dd>' in response.content.decode()


def test_plan_from_a_different_product_is_rejected(client, course, offer):
    """A plan_id belonging to another product's page must not be accepted here."""
    other_course = Course.objects.create(cf_course_id="crs_other", name="Other", workspace_id="ws_1")
    other_plan = PaymentPlan.objects.create(
        code="other-plan", name="Other Plan", course=other_course,
        installment_count=1, installment_amount=Decimal("10000.00"), is_active=True,
    )
    response = client.post(CHECKOUT_URL, {
        "plan_id": other_plan.id, "email": "ada@example.com", "idempotency_key": "cross-product",
    })
    assert response.status_code == 200
    assert response.context["form"].errors.get("plan_id")


def test_checkout_page_uses_public_base_template_not_staff_portal(client, course, offer):
    """The staff sidebar/topbar (Contacts/Courses/Enrollments nav) must never appear on a public checkout page."""
    PaymentPlan.objects.create(
        code="shell-plan", name="Shell Plan", course=course,
        installment_count=1, installment_amount=Decimal("50000.00"), is_active=True,
    )
    response = client.get(CHECKOUT_URL)
    content = response.content.decode()
    assert "sidebar-wrapper" not in content
    assert "Django Admin" not in content


def test_multiple_active_plans_all_rendered_as_selectable_options(client, course, offer):
    """Plans render as radio inputs sharing one name — the browser can select exactly one."""
    PaymentPlan.objects.create(
        code="plan-a", name="Plan A", course=course,
        installment_count=1, installment_amount=Decimal("10000.00"), is_active=True,
    )
    PaymentPlan.objects.create(
        code="plan-b", name="Plan B", course=course,
        installment_count=2, installment_amount=Decimal("20000.00"), is_active=True,
    )
    response = client.get(CHECKOUT_URL)
    content = response.content.decode()
    assert content.count('name="plan_id"') == 2
    assert "En 1 fois" in content
    assert "En 2 fois" in content


def test_first_plan_preselected_on_initial_load(client, course, offer):
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


def test_checkout_validation_and_help_are_french_without_changing_other_views(client, course, offer):
    from django.utils import translation

    plan = PaymentPlan.objects.create(
        code="french", name="Paiement en une fois", course=course,
        installment_count=1, installment_amount=Decimal("50000"), is_active=True,
    )
    with translation.override("en"):
        response = client.post(CHECKOUT_URL, {
            "plan_id": plan.id, "email": "invalide", "idempotency_key": "french-validation",
        })
        assert translation.get_language() == "en"
    content = response.content.decode()
    assert 'lang="fr"' in content
    assert "valide" in content
    assert "Enter a valid" not in content
    assert 'aria-describedby="id_email_helptext id_email_error"' in content
    assert "Passer au paiement sécurisé" in content
