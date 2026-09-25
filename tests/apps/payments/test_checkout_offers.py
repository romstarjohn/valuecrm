from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse

from apps.courses.models import Course, CheckoutOffer, CheckoutBenefit
from apps.courses.services import CourseService
from apps.payments.models import PaymentPlan
from integrations.clickfunnels.schemas import CourseDTO

pytestmark = pytest.mark.django_db


@pytest.fixture
def default_offer():
    CheckoutOffer.objects.filter(is_default=True).update(is_default=False)
    offer = CheckoutOffer.objects.create(
        slug="default-test", is_default=True, title="Default tropical program",
        mockup_image="images/checkout/monafrolibre-tropical-course.jpg", mockup_alt="Default course mockup",
        highlights="No experience required\nLearn at your pace",
    )
    CheckoutBenefit.objects.create(offer=offer, title="Ten hours of video", description="Step-by-step lessons.")
    return offer


def purchasable_course(identifier="1", name=None):
    course = Course.objects.create(cf_course_id=identifier, name=name or f"Course {identifier}", workspace_id="ws")
    PaymentPlan.objects.create(
        code=f"plan-{identifier}", name="Pay in full", course=course,
        installment_amount=Decimal("100000"), installment_count=1, is_active=True,
    )
    return course


def checkout_url(course):
    return reverse("payments:checkout_start", args=[course.slug])


def test_new_courses_inherit_default_mockup_and_benefits(client, default_offer):
    course = purchasable_course()
    response = client.get(checkout_url(course))
    assert response.context["course_presentations"] == [{"course": course, "offer": default_offer}]
    assert b"Ten hours of video" in response.content
    assert b"monafrolibre-tropical-course.jpg" in response.content


def test_course_override_replaces_default_only_for_its_course(client, default_offer):
    """Two products, each at its own URL — the override never leaks onto the default-using course's page, or vice versa."""
    first = purchasable_course("first")
    second = purchasable_course("second")
    override = CheckoutOffer.objects.create(
        course=second, slug="second", title="A different course", mockup_image="https://example.com/course.jpg",
        mockup_alt="Another product mockup",
    )
    CheckoutBenefit.objects.create(offer=override, title="Private workshops", description="Workshop materials.")

    first_response = client.get(checkout_url(first))
    assert b"Ten hours of video" in first_response.content
    assert b"Private workshops" not in first_response.content

    second_response = client.get(checkout_url(second))
    assert b"Private workshops" in second_response.content
    assert b"Ten hours of video" not in second_response.content


def test_offer_is_rendered_once_per_course_not_once_per_plan(client, default_offer):
    course = purchasable_course()
    PaymentPlan.objects.create(code="split", name="Installments", course=course, installment_count=3, installment_amount=Decimal("34000"), is_active=True)
    response = client.get(checkout_url(course))
    assert response.content.count(b"Ten hours of video") == 1


def test_inactive_course_content_is_not_exposed(client, default_offer):
    course = purchasable_course()
    PaymentPlan.objects.filter(course=course).update(is_active=False)
    response = client.get(checkout_url(course))
    assert b"Ten hours of video" not in response.content
    assert b"monafrolibre-tropical-course.jpg" not in response.content
    assert b"Aucun plan disponible" in response.content


def test_unknown_slug_returns_404(client):
    assert client.get(reverse("payments:checkout_start", args=["does-not-exist"])).status_code == 404


def test_sync_preserves_course_offer_and_benefits(default_offer):
    course = Course.objects.create(cf_course_id="1", name="Sync course", workspace_id="ws")
    PaymentPlan.objects.create(code="sync-plan", name="Pay in full", course=course, installment_amount=Decimal("100000"), installment_count=1, is_active=True)
    default_offer.course = course
    default_offer.save()
    client = MagicMock()
    client.list_courses.return_value = [CourseDTO(id=1, title="New synced title", description="New synced description")]
    CourseService(client).sync_courses(workspace_id=1, workspace_subdomain="ws")
    course.refresh_from_db()
    assert course.name == "New synced title"
    assert course.checkout_offer == default_offer
    assert course.checkout_offer.benefits.get().title == "Ten hours of video"


def test_offer_text_is_escaped(client, default_offer):
    course = purchasable_course()
    default_offer.title = '<img src=x onerror="alert(1)">'
    default_offer.save()
    content = client.get(checkout_url(course)).content.decode()
    assert '<img src=x onerror="alert(1)">' not in content
    assert '&lt;img src=x onerror=' in content


@pytest.mark.parametrize("image", ["javascript:alert(1)", "//example.com/image.jpg", "images/../../private.png"])
def test_mockup_source_rejects_unsafe_paths(default_offer, image):
    default_offer.mockup_image = image
    with pytest.raises(ValidationError):
        default_offer.full_clean()


def test_only_one_default_offer_can_be_saved(default_offer):
    with transaction.atomic(), pytest.raises(IntegrityError):
        CheckoutOffer.objects.create(slug="duplicate-default", title="Other", is_default=True)


# --- Shop index (product listing) ---

def test_shop_index_lists_every_course_with_an_active_plan(client, default_offer):
    """Both a course with its own offer and one relying on the shared default appear."""
    plain = purchasable_course("plain", name="Plain course")
    customized = purchasable_course("customized", name="Customized course")
    CheckoutOffer.objects.create(
        course=customized, slug="customized-offer", title="Customized Product",
        mockup_image="https://example.com/course.jpg", mockup_alt="mockup",
    )
    response = client.get(reverse("payments:shop_index"))
    assert response.status_code == 200
    assert default_offer.title.encode() in response.content  # the "plain" course's card, via fallback
    assert b"Customized Product" in response.content
    assert checkout_url(plain).encode() in response.content
    assert checkout_url(customized).encode() in response.content


def test_shop_index_excludes_course_with_no_active_plans(client, default_offer):
    course = purchasable_course("unsellable", name="Unsellable course")
    PaymentPlan.objects.filter(course=course).update(is_active=False)
    response = client.get(reverse("payments:shop_index"))
    assert b"Unsellable course" not in response.content


def test_shop_index_empty_state_when_no_products_qualify(client):
    response = client.get(reverse("payments:shop_index"))
    assert response.status_code == 200
    assert b"Aucune formation disponible" in response.content
