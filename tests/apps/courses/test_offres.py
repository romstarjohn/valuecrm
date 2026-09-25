"""The "Offres" area: offer list, offer page, sales-page form (image upload), price form, old-page redirects."""
import io
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from apps.courses.forms import MAX_OFFER_IMAGE_BYTES, CheckoutOfferForm
from apps.courses.models import CheckoutBenefit, CheckoutOffer, Course
from apps.payments.forms import PaymentPlanForm
from apps.payments.models import PaymentPlan

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"


def image_file(name="visuel.png", fmt="PNG", size=(40, 30)):
    buffer = io.BytesIO()
    Image.new("RGB", size, "green").save(buffer, format=fmt)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f"image/{fmt.lower()}")


def make_course(identifier="crs_1", name="Cheveux crépus"):
    return Course.objects.create(cf_course_id=identifier, name=name, workspace_id="ws")


def make_plan(course, count=1, amount="1000", active=True, code=None):
    return PaymentPlan.objects.create(
        code=code or f"plan-{course.pk}-{count}-{amount}", name="Formule", course=course,
        installment_count=count, installment_amount=Decimal(amount), is_active=active,
    )


def make_offer(course, **kwargs):
    defaults = dict(slug=f"offre-{course.pk}", title="Titre de vente", mockup_image="images/x.jpg", mockup_alt="alt")
    defaults.update(kwargs)
    return CheckoutOffer.objects.create(course=course, **defaults)


def offer_post(title="Ma page", benefits=(), **extra):
    data = {
        "title": title, "subtitle": "", "language": "fr", "mockup_alt": "", "highlights": "", "closing_note": "",
        "benefits-TOTAL_FORMS": str(len(benefits)), "benefits-INITIAL_FORMS": "0",
        "benefits-MIN_NUM_FORMS": "0", "benefits-MAX_NUM_FORMS": "1000",
    }
    for i, (btitle, order) in enumerate(benefits):
        data[f"benefits-{i}-title"] = btitle
        data[f"benefits-{i}-description"] = f"Description {btitle}"
        data[f"benefits-{i}-ORDER"] = str(order)
    data.update(extra)
    return data


# --- Offres list ---

def test_offres_list_shows_readiness_and_next_step(superuser_client):
    ready = make_course("c-ready", "Prête")
    make_offer(ready)
    make_plan(ready)
    no_page = make_course("c-nopage", "Sans page")
    make_plan(no_page)
    nothing = make_course("c-nothing", "Rien")

    content = superuser_client.get(reverse("courses:list")).content.decode()

    assert "Prête à vendre" in content
    assert "À compléter : la page de vente" in content
    assert "À compléter : la page de vente et un prix" in content
    assert f"{reverse('courses:product_add')}?course_id={no_page.pk}" in content
    assert f"{reverse('courses:product_add')}?course_id={nothing.pk}" in content
    assert "1 × 1 000 XAF" in content
    assert "Copier le lien" in content
    assert "c-ready" not in content.split("<tbody>")[1].split("onclick")[0]  # no ClickFunnels id as visible text


def test_offres_list_price_missing_points_to_add_price(superuser_client):
    course = make_course()
    make_offer(course)

    content = superuser_client.get(reverse("courses:list")).content.decode()

    assert "À compléter : un prix" in content
    assert f"{reverse('plans:add')}?course_id={course.pk}" in content


def test_offres_list_empty_state_explains_clickfunnels(superuser_client):
    content = superuser_client.get(reverse("courses:list")).content.decode()
    assert "Importer depuis ClickFunnels" in content
    assert "Connectez ClickFunnels" in content


# --- Offre page ---

def test_offer_page_steps_without_anything(superuser_client):
    course = make_course()
    content = superuser_client.get(reverse("courses:detail", args=[course.cf_course_id])).content.decode()
    assert "Créer la page de vente" in content
    assert "Ajouter un prix" in content
    assert "Le lien sera disponible dès qu'un prix sera en vente" in content
    assert "Détails techniques" in content


def test_offer_page_ready(superuser_client):
    course = make_course()
    make_offer(course)
    make_plan(course, count=3, amount="20000")
    make_plan(course, amount="50000", active=False)

    content = superuser_client.get(reverse("courses:detail", args=[course.cf_course_id])).content.decode()

    assert "Modifier la page de vente" in content
    assert "3 × 20 000 XAF, tous les 30 jours" in content
    assert "En vente" in content and "Masquée" in content
    assert reverse("payments:checkout_start", args=[course.slug]) in content
    assert "Ouvrir la page comme un client" in content


# --- Page de vente form ---

def test_create_page_with_uploaded_image_and_auto_slug(superuser_client):
    course = make_course()
    url = reverse("courses:product_add") + f"?course_id={course.pk}"

    page = superuser_client.get(url).content.decode()
    assert 'enctype="multipart/form-data"' in page
    assert "Identifiant interne" not in page
    assert 'name="course"' not in page  # course fixed when opened from its offer page

    response = superuser_client.post(url, offer_post(image=image_file()))

    assert response.status_code == 302
    assert response.url == reverse("courses:detail", args=[course.cf_course_id])
    offer = CheckoutOffer.objects.get(course=course)
    assert offer.slug == "ma-page"
    assert offer.image.name.startswith("offers/")
    assert offer.image_url.startswith("/media/offers/")
    assert offer.mockup_alt == "Ma page"  # alt text defaults to the title


def test_image_is_required_for_a_new_page(superuser_client):
    course = make_course()
    response = superuser_client.post(reverse("courses:product_add") + f"?course_id={course.pk}", offer_post())
    assert response.status_code == 200
    assert "Ajoutez une image" in response.content.decode()
    assert not CheckoutOffer.objects.filter(course=course).exists()


def test_image_type_is_validated():
    course = make_course()
    fake = SimpleUploadedFile("virus.png", b"not an image at all", content_type="image/png")
    form = CheckoutOfferForm(offer_post(), {"image": fake}, course=course)
    assert not form.is_valid()
    assert "image" in form.errors


def test_gif_is_rejected_with_clear_message():
    course = make_course()
    form = CheckoutOfferForm(offer_post(), {"image": image_file("anim.gif", "GIF")}, course=course)
    assert not form.is_valid()
    assert "JPG, PNG ou WEBP" in str(form.errors["image"])


def test_image_size_is_validated():
    course = make_course()
    big = image_file()
    big.size = MAX_OFFER_IMAGE_BYTES + 1
    form = CheckoutOfferForm(offer_post(), {"image": big}, course=course)
    assert not form.is_valid()
    assert "3 Mo" in str(form.errors["image"])


def test_benefit_order_follows_row_order(superuser_client):
    course = make_course()
    url = reverse("courses:product_add") + f"?course_id={course.pk}"
    # Row 0 moved below row 1 by the user: ORDER carries the on-screen position.
    superuser_client.post(url, offer_post(image=image_file(), benefits=[("Deuxième", 2), ("Premier", 1)]))

    benefits = list(CheckoutBenefit.objects.filter(offer__course=course).order_by("display_order"))
    assert [b.title for b in benefits] == ["Premier", "Deuxième"]
    assert [b.display_order for b in benefits] == [0, 1]


def test_existing_page_keeps_image_when_no_new_file(superuser_client):
    course = make_course()
    offer = make_offer(course)
    response = superuser_client.post(reverse("courses:product_edit", args=[offer.pk]), offer_post(title="Nouveau titre"))
    assert response.status_code == 302
    offer.refresh_from_db()
    assert offer.title == "Nouveau titre"
    assert offer.mockup_image == "images/x.jpg"
    assert offer.slug == f"offre-{course.pk}"  # existing slug never changes


def test_create_for_course_with_page_redirects_to_edit(superuser_client):
    course = make_course()
    offer = make_offer(course)
    response = superuser_client.get(reverse("courses:product_add") + f"?course_id={course.pk}")
    assert response.url == reverse("courses:product_edit", args=[offer.pk])


def test_media_files_are_served(superuser_client, client):
    course = make_course()
    superuser_client.post(reverse("courses:product_add") + f"?course_id={course.pk}", offer_post(image=image_file()))
    offer = CheckoutOffer.objects.get(course=course)
    response = client.get(offer.image_url)
    assert response.status_code == 200


# --- Formule de prix form ---

def plan_post(mode, **extra):
    data = {
        "payment_mode": mode, "name": "Formule", "description": "",
        "installment_amount": "20000", "access_policy": "FULL_PAYMENT", "is_active": "on",
    }
    data.update(extra)
    return data


def test_plan_form_single_payment_forces_one_installment():
    course = make_course()
    form = PaymentPlanForm(plan_post("once", installment_count="5"), course=course)
    assert form.is_valid(), form.errors
    plan = form.save()
    assert plan.installment_count == 1
    assert plan.code == "formule"
    assert plan.course == course


def test_plan_form_several_payments_requires_count_and_interval():
    course = make_course()
    assert not PaymentPlanForm(plan_post("several", installment_count="1", installment_interval_days="30"), course=course).is_valid()
    assert not PaymentPlanForm(plan_post("several", installment_count="3"), course=course).is_valid()

    form = PaymentPlanForm(plan_post("several", installment_count="3", installment_interval_days="30"), course=course)
    assert form.is_valid(), form.errors
    plan = form.save()
    assert (plan.installment_count, plan.installment_interval_days) == (3, 30)
    assert plan.computed_total == Decimal("60000")


def test_plan_create_returns_to_offer_page(superuser_client):
    course = make_course()
    url = reverse("plans:add") + f"?course_id={course.pk}"
    page = superuser_client.get(url).content.decode()
    assert "Comment le client paie-t-il ?" in page
    assert "Code interne" not in page

    response = superuser_client.post(url, plan_post("once"))

    assert response.status_code == 302
    assert response.url == reverse("courses:detail", args=[course.cf_course_id])
    assert PaymentPlan.objects.filter(course=course, installment_count=1).exists()


def test_plan_edit_keeps_several_mode(superuser_client):
    course = make_course()
    plan = make_plan(course, count=3, amount="20000")
    page = superuser_client.get(reverse("plans:edit", args=[plan.pk])).content.decode()
    assert 'value="several" checked' in page or 'checked value="several"' in page or ('value="several"' in page and "checked" in page)


# --- Old pages ---

def test_old_products_and_plans_lists_redirect_to_offres(superuser_client):
    for url in (reverse("courses:product_list"), reverse("plans:list")):
        response = superuser_client.get(url)
        assert response.status_code == 302
        assert response.url == reverse("courses:list")


def test_old_lists_keep_permission_checks(staff_client):
    assert staff_client.get(reverse("courses:product_list")).status_code == 403
    assert staff_client.get(reverse("plans:list")).status_code == 403


def test_offer_panel_renders_in_plain_words(superuser_client):
    course = make_course()
    make_plan(course)
    payload = superuser_client.get(reverse("courses:detail_panel", args=[course.cf_course_id])).json()
    assert "Ajouter un prix" not in payload["body_html"]
    assert "Copier le lien" in payload["body_html"]
    assert "ClickFunnels" not in payload["subtitle"]
