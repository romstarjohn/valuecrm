from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Prefetch, Q
from django.http import JsonResponse
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.timesince import timesince
from .forms import CheckoutBenefitFormSet, CheckoutOfferForm, save_benefits_in_row_order
from .models import CheckoutOffer, Course
from .services import CourseService
from apps.configuration.services import ConfigurationService
from apps.enrollments.models import EnrollmentAttempt
from apps.payments.models import PaymentPlan
from integrations.clickfunnels.client import ClickFunnelsClient

COURSE_SORT_FIELDS = {
    "name": ["name"],
    "workspace": ["workspace_id"],
    "enrollments": ["active_enrollment_count"],
    "updated": ["updated_at"],
}

def _querystring(request, *, exclude=()):
    """Current GET params, minus `exclude`, for building links that keep
    every other active filter/sort param (pagination, sort-column links)."""
    params = request.GET.copy()
    for key in exclude:
        params.pop(key, None)
    return params.urlencode()

def _filter_options(request, *, param, options, all_label):
    """(label, url, active) rows for a single-select querystring filter,
    preserving every other active filter/sort param and resetting to page 1."""
    current = request.GET.get(param, "")
    base_params = request.GET.copy()
    base_params.pop("page", None)

    all_params = base_params.copy()
    all_params.pop(param, None)
    rows = [{"label": all_label, "url": "?" + all_params.urlencode(), "active": not current}]

    for value in options:
        opt_params = base_params.copy()
        opt_params[param] = value
        rows.append({"label": value, "url": "?" + opt_params.urlencode(), "active": current == value})
    return rows

def describe_plan(plan) -> str:
    """"1 × 1 000 XAF" / "3 × 20 000 XAF, tous les 30 jours" — prices as a customer reads them."""
    amount = f"{plan.installment_amount:,.0f}".replace(",", "\u202f")
    if plan.installment_count == 1:
        return f"1 × {amount} {plan.currency}"
    return f"{plan.installment_count} × {amount} {plan.currency}, tous les {plan.installment_interval_days} jours"


def offer_readiness(course, offer, active_plans) -> dict:
    """
    What a layperson needs to know about one offer: can it be sold, and if
    not, the single next step. Mirrors what the public checkout needs: an
    active formule de prix (without one the link shows nothing to buy) and a
    page de vente of its own (otherwise the shared default page is shown).
    """
    missing = []
    if offer is None:
        missing.append(("la page de vente", "page"))
    if not active_plans:
        missing.append(("un prix", "price"))
    if not missing:
        return {"ready": True, "label": "Prête à vendre", "next_step": None}
    return {
        "ready": False,
        "label": "À compléter : " + " et ".join(label for label, _ in missing),
        "next_step": missing[0][1],
    }


@login_required
def course_list(request):
    """
    "Offres" — one row per course as it is sold (docs/UI_VOCABULARY.md):
    its page de vente, its prices, its payment link and whether anything is
    still missing. ClickFunnels identifiers are deliberately not shown here.
    """
    query = request.GET.get("q", "")

    courses = Course.objects.select_related("checkout_offer").annotate(
        active_enrollment_count=Count(
            "enrollment_attempts",
            filter=Q(enrollment_attempts__status=EnrollmentAttempt.Status.SUCCESS, enrollment_attempts__cf_suspended=False),
            distinct=True,
        ),
    ).prefetch_related(
        Prefetch(
            "payment_plans",
            queryset=PaymentPlan.objects.filter(is_active=True).order_by("display_order", "installment_amount"),
            to_attr="active_plans",
        ),
    )
    if query:
        courses = courses.filter(Q(name__icontains=query) | Q(checkout_offer__title__icontains=query))
    courses = courses.order_by("name")

    paginator = Paginator(courses, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    rows = []
    for course in page_obj:
        offer = getattr(course, "checkout_offer", None)
        plans = course.active_plans
        if not plans:
            price = ""
        elif len(plans) == 1:
            price = describe_plan(plans[0])
        else:
            price = f"{len(plans)} formules"
        rows.append({
            "course": course,
            "offer": offer,
            "price": price,
            "readiness": offer_readiness(course, offer, plans),
            "checkout_url": request.build_absolute_uri(reverse("payments:checkout_start", args=[course.slug])) if plans else "",
        })

    context = {
        "page_obj": page_obj,
        "rows": rows,
        "query": query,
        "querystring": _querystring(request, exclude=("page",)),
        "can_add_offer": request.user.has_perm("courses.add_checkoutoffer"),
        "can_add_plan": request.user.has_perm("payments.add_paymentplan"),
    }
    return render(request, "courses/list.html", context)

@login_required
def course_detail(request, cf_course_id):
    """
    The Offre page: everything about selling one course, as three steps —
    ① page de vente ② formules de prix ③ lien de paiement — then who has
    access. ClickFunnels/technical identifiers sit in a collapsed section.
    """
    course = get_object_or_404(Course.objects.select_related("checkout_offer"), cf_course_id=cf_course_id)
    offer = getattr(course, "checkout_offer", None)
    plans = list(PaymentPlan.objects.filter(course=course).order_by("-is_active", "display_order", "installment_amount"))
    for plan in plans:
        plan.description_text = describe_plan(plan)
    active_plans = [p for p in plans if p.is_active]
    checkout_url = request.build_absolute_uri(reverse("payments:checkout_start", args=[course.slug]))
    recent_enrollments = course.enrollment_attempts.all().select_related("contact")[:10]
    active_enrollment_count = course.enrollment_attempts.filter(
        status=EnrollmentAttempt.Status.SUCCESS, cf_suspended=False,
    ).count()

    can_add_offer = request.user.has_perm("courses.add_checkoutoffer")
    can_edit_offer = request.user.has_perm("courses.change_checkoutoffer")
    can_add_plan = request.user.has_perm("payments.add_paymentplan")

    context = {
        "course": course,
        "offer": offer,
        "plans": plans,
        "active_plans": active_plans,
        "readiness": offer_readiness(course, offer, active_plans),
        "checkout_url": checkout_url,
        "recent_enrollments": recent_enrollments,
        "active_enrollment_count": active_enrollment_count,
        "can_add_offer": can_add_offer,
        "can_edit_offer": can_edit_offer,
        "can_add_plan": can_add_plan,
        "can_edit_plan": request.user.has_perm("payments.change_paymentplan"),
        "add_offer_url": reverse("courses:product_add") + f"?course_id={course.pk}" if can_add_offer else "",
        "add_plan_url": reverse("plans:add") + f"?course_id={course.pk}" if can_add_plan else "",
        "admin_edit_url": reverse("admin:courses_course_change", args=[course.pk]) if request.user.is_superuser else "",
    }
    return render(request, "courses/detail.html", context)


@login_required
def course_detail_panel(request, cf_course_id):
    """
    JSON payload for the Cours list's slide-over (docs/PRODUCT_CADRAGE_PMI.md-
    driven dashboard layout). Same data as course_detail's card, just shaped
    for the shell's generic [data-panel-url] opener (static/js/slideover.js)
    instead of a full page. The "Modifier" action links to the Django admin
    Course change form — the only place slug/description are actually
    editable today; never fabricate a separate edit form here.
    """
    course = get_object_or_404(Course, cf_course_id=cf_course_id)
    plans = PaymentPlan.objects.filter(course=course, is_active=True).order_by("name")
    active_enrollment_count = EnrollmentAttempt.objects.filter(
        course=course, status=EnrollmentAttempt.Status.SUCCESS, cf_suspended=False,
    ).count()
    checkout_url = request.build_absolute_uri(reverse("payments:checkout_start", args=[course.slug]))

    actions_html = ""
    if request.user.is_superuser:
        admin_url = reverse("admin:courses_course_change", args=[course.pk])
        actions_html = (
            f'<a href="{admin_url}" target="_blank" rel="noopener" class="btn-crm btn-crm-primary btn-crm-sm">'
            f'<i class="fa-solid fa-pen me-1 small" aria-hidden="true"></i>Modifier dans Django</a>'
        )

    can_add_plan = request.user.has_perm("payments.add_paymentplan")
    body_html = render_to_string(
        "courses/_detail_panel_body.html",
        {
            "course": course, "plans": plans,
            "active_enrollment_count": active_enrollment_count, "checkout_url": checkout_url,
            "can_add_plan": can_add_plan,
            "add_plan_url": reverse("plans:add") + f"?course_id={course.pk}" if can_add_plan else "",
        },
        request=request,
    )

    return JsonResponse({
        "title": course.name,
        "subtitle": "Offre",
        "actions_html": actions_html,
        "body_html": body_html,
    })

@login_required
def course_sync(request):
    """
    View to trigger course sync from ClickFunnels.
    Aligns with multi-step config and subdomain requirements.
    """
    config_service = ConfigurationService()
    active_config = config_service.get_active_config()
    
    if request.method == "POST":
        if not active_config:
            messages.error(request, "Aucune configuration ClickFunnels active trouvée.")
            return redirect("courses:sync")

        # Validation based on refactored config structure
        if not active_config.workspace_id:
            messages.error(request, "Sélectionnez un espace de travail avant de synchroniser les formations.")
            return redirect("configuration:settings")

        if not active_config.workspace_subdomain:
            messages.error(request, "Le sous-domaine de l'espace de travail est manquant. Les appels API au niveau de l'espace de travail ne peuvent pas continuer.")
            return redirect("configuration:settings")

        try:
            client = ClickFunnelsClient.from_configuration(active_config)
            service = CourseService(client=client)
            # Use data from active config
            result = service.sync_courses(
                workspace_id=int(active_config.workspace_id),
                workspace_subdomain=active_config.workspace_subdomain
            )
            messages.success(
                request,
                f"{result['synced_count']} formations synchronisées avec succès "
                f"({result['created_count']} nouvelles, {result['updated_count']} mises à jour)."
            )
            return redirect("courses:list")
        except Exception as e:
            messages.error(request, f"Erreur lors de la synchronisation des formations : {str(e)}")
            return redirect("courses:sync")

    context = {
        "active_config": active_config,
    }
    return render(request, "courses/sync.html", context)


@login_required
def product_list(request):
    """Former "Produits" list — merged into the Offres list (courses:list)."""
    if not request.user.has_perm("courses.view_checkoutoffer"):
        raise PermissionDenied
    return redirect("courses:list")


def _save_product_form(form, formset):
    offer = form.save()
    save_benefits_in_row_order(formset, offer)
    return offer


@login_required
def product_create(request):
    if not request.user.has_perm("courses.add_checkoutoffer"):
        raise PermissionDenied

    course = None
    course_id = request.GET.get("course_id", "")
    if course_id.isdigit():
        course = get_object_or_404(Course, pk=int(course_id))
        existing = getattr(course, "checkout_offer", None)
        if existing is not None:
            return redirect("courses:product_edit", pk=existing.pk)

    if request.method == "POST":
        form = CheckoutOfferForm(request.POST, request.FILES, course=course)
        formset = CheckoutBenefitFormSet(request.POST, instance=form.instance)
        if form.is_valid() and formset.is_valid():
            offer = _save_product_form(form, formset)
            messages.success(request, f"La page de vente de « {offer.course.name} » est créée.")
            return redirect("courses:detail", cf_course_id=offer.course.cf_course_id)
    else:
        form = CheckoutOfferForm(course=course)
        formset = CheckoutBenefitFormSet(instance=form.instance)

    return render(request, "courses/product_form.html", {
        "form": form, "formset": formset, "course": course,
        "title": f"Page de vente — {course.name}" if course else "Nouvelle page de vente",
        "is_create": True,
    })


@login_required
def product_update(request, pk):
    offer = get_object_or_404(CheckoutOffer.objects.select_related("course"), pk=pk)
    if not request.user.has_perm("courses.change_checkoutoffer"):
        raise PermissionDenied

    if request.method == "POST":
        form = CheckoutOfferForm(request.POST, request.FILES, instance=offer)
        formset = CheckoutBenefitFormSet(request.POST, instance=offer)
        if form.is_valid() and formset.is_valid():
            offer = _save_product_form(form, formset)
            messages.success(request, "Page de vente enregistrée.")
            if offer.course:
                return redirect("courses:detail", cf_course_id=offer.course.cf_course_id)
            return redirect("courses:product_edit", pk=offer.pk)
    else:
        form = CheckoutOfferForm(instance=offer)
        formset = CheckoutBenefitFormSet(instance=offer)

    checkout_url = (
        request.build_absolute_uri(reverse("payments:checkout_start", args=[offer.course.slug])) if offer.course else ""
    )
    return render(request, "courses/product_form.html", {
        "form": form, "formset": formset, "offer": offer, "course": offer.course,
        "title": f"Page de vente — {offer.course.name}" if offer.course else f"Page de vente — {offer.title}",
        "is_create": False, "checkout_url": checkout_url,
    })
