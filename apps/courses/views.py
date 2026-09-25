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
from .forms import CheckoutBenefitFormSet, CheckoutOfferForm
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

@login_required
def course_list(request):
    """
    CRM List View for Courses with search and filtering.
    """
    query = request.GET.get("q", "")
    workspace_filter = request.GET.get("workspace_id", "")

    courses = Course.objects.all().annotate(
        active_enrollment_count=Count(
            "enrollment_attempts",
            filter=Q(enrollment_attempts__status=EnrollmentAttempt.Status.SUCCESS, enrollment_attempts__cf_suspended=False),
            distinct=True,
        ),
    ).prefetch_related(
        Prefetch(
            "payment_plans",
            queryset=PaymentPlan.objects.filter(is_active=True).order_by("name"),
            to_attr="active_plans",
        ),
    )

    if query:
        courses = courses.filter(
            Q(name__icontains=query) |
            Q(cf_course_id__icontains=query)
        )

    if workspace_filter:
        courses = courses.filter(workspace_id=workspace_filter)

    sort_key = request.GET.get("sort", "")
    sort_dir = request.GET.get("dir", "asc")
    if sort_key in COURSE_SORT_FIELDS:
        order_fields = COURSE_SORT_FIELDS[sort_key]
        if sort_dir == "desc":
            order_fields = [f"-{field}" for field in order_fields]
        courses = courses.order_by(*order_fields)
    else:
        sort_key = ""
        sort_dir = ""
        courses = courses.order_by("name")

    paginator = Paginator(courses, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    # .order_by() clears Course.Meta.ordering (["name"]) before .distinct() —
    # otherwise Django folds name into the SELECT DISTINCT and courses
    # sharing a workspace_id never collapse into one row (same fix as
    # apps/contacts/views.py::contact_list's status_options).
    workspace_options = list(
        Course.objects.exclude(workspace_id="").order_by().values_list("workspace_id", flat=True).distinct()
    )

    headers = [
        {"label": "Cours", "sortable": True, "sort_key": "name"},
        {"label": "Réf. ClickFunnels", "sortable": False},
        {"label": "Synchronisation", "sortable": True, "sort_key": "updated"},
        {"label": "Plan", "sortable": False},
        {"label": "Inscrits actifs", "sortable": True, "sort_key": "enrollments", "align": "num"},
    ]

    context = {
        "page_obj": page_obj,
        "query": query,
        "workspace_filter": workspace_filter,
        "workspace_options": workspace_options,
        "workspace_filter_options": _filter_options(request, param="workspace_id", options=workspace_options, all_label="Tous les espaces de travail"),
        "workspace_label": f"Espace de travail : {workspace_filter or 'Tous'}",
        "headers": headers,
        "sort_key": sort_key,
        "sort_dir": sort_dir,
        "sort_qs": _querystring(request, exclude=("page", "sort", "dir")),
        "querystring": _querystring(request, exclude=("page",)),
    }
    return render(request, "courses/list.html", context)

@login_required
def course_detail(request, cf_course_id):
    """
    Detailed course view with recent enrollment attempts.
    """
    course = get_object_or_404(Course, cf_course_id=cf_course_id)
    recent_enrollments = course.enrollment_attempts.all().select_related("contact")[:10]
    plans = PaymentPlan.objects.filter(course=course, is_active=True).order_by("name")
    checkout_path = reverse("payments:checkout_start", args=[course.slug])
    checkout_url = request.build_absolute_uri(checkout_path)

    course_subtitle = f"réf. ClickFunnels {course.cf_course_id} · synchronisé il y a {timesince(course.updated_at)}"

    total_attempts = course.enrollment_attempts.count()
    success_attempts = course.enrollment_attempts.filter(status=EnrollmentAttempt.Status.SUCCESS).count()
    active_enrollment_count = course.enrollment_attempts.filter(
        status=EnrollmentAttempt.Status.SUCCESS, cf_suspended=False,
    ).count()
    success_rate = round(success_attempts / total_attempts * 100) if total_attempts else None

    context = {
        "course": course,
        "course_subtitle": course_subtitle,
        "recent_enrollments": recent_enrollments,
        "plans": plans,
        "checkout_url": checkout_url,
        "total_attempts": total_attempts,
        "active_enrollment_count": active_enrollment_count,
        "success_rate": success_rate,
        "can_edit": request.user.is_superuser,
        "edit_url": reverse("admin:courses_course_change", args=[course.pk]) if request.user.is_superuser else "",
        # Plan/produit management is real gestionnaire work (docs/PRODUCT_CADRAGE_PMI.md
        # §3), not an administrator-only action like editing the CF-owned
        # Course record above — gated on the real permission, not is_superuser.
        "can_add_plan": request.user.has_perm("payments.add_paymentplan"),
        "add_plan_url": (
            reverse("plans:add") + f"?course_id={course.pk}" if request.user.has_perm("payments.add_paymentplan") else ""
        ),
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
            f'<i class="fa-solid fa-pen me-1 small" aria-hidden="true"></i>Modifier</a>'
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
        "subtitle": f"réf. ClickFunnels {course.cf_course_id}",
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
    """
    One row per course — the produit (docs/PRODUCT_CADRAGE_PMI.md §4). Shows
    every course whether or not it already has a CheckoutOffer, same "what
    still needs configuring" philosophy as apps.payments.staff_views.plan_list.
    """
    if not request.user.has_perm("courses.view_checkoutoffer"):
        raise PermissionDenied

    offers_by_course_id = {
        offer.course_id: offer
        for offer in CheckoutOffer.objects.filter(course__isnull=False)
    }
    plan_counts = {
        row["course_id"]: row["n"]
        for row in PaymentPlan.objects.filter(is_active=True).values("course_id").annotate(n=Count("id"))
    }
    rows = [
        {"course": course, "offer": offers_by_course_id.get(course.pk), "active_plan_count": plan_counts.get(course.pk, 0)}
        for course in Course.objects.order_by("name")
    ]

    context = {
        "rows": rows,
        "can_add": request.user.has_perm("courses.add_checkoutoffer"),
        "can_edit": request.user.has_perm("courses.change_checkoutoffer"),
    }
    return render(request, "courses/product_list.html", context)


def _save_product_form(request, form, formset):
    offer = form.save()
    formset.instance = offer
    formset.save()
    return offer


@login_required
def product_create(request):
    if not request.user.has_perm("courses.add_checkoutoffer"):
        raise PermissionDenied

    initial = {}
    course_id = request.GET.get("course_id", "")
    if course_id.isdigit():
        initial["course"] = course_id

    if request.method == "POST":
        form = CheckoutOfferForm(request.POST)
        formset = CheckoutBenefitFormSet(request.POST, instance=form.instance)
        if form.is_valid() and formset.is_valid():
            offer = _save_product_form(request, form, formset)
            messages.success(request, f"Produit « {offer.title} » créé pour {offer.course.name}.")
            return redirect("courses:product_edit", pk=offer.pk)
    else:
        form = CheckoutOfferForm(initial=initial)
        formset = CheckoutBenefitFormSet(instance=form.instance)

    return render(request, "courses/product_form.html", {
        "form": form, "formset": formset, "title": "Nouveau produit", "is_create": True,
    })


@login_required
def product_update(request, pk):
    offer = get_object_or_404(CheckoutOffer.objects.select_related("course"), pk=pk)
    if not request.user.has_perm("courses.change_checkoutoffer"):
        raise PermissionDenied

    if request.method == "POST":
        form = CheckoutOfferForm(request.POST, instance=offer)
        formset = CheckoutBenefitFormSet(request.POST, instance=offer)
        if form.is_valid() and formset.is_valid():
            offer = _save_product_form(request, form, formset)
            messages.success(request, f"Produit « {offer.title} » mis à jour.")
            return redirect("courses:product_edit", pk=offer.pk)
    else:
        form = CheckoutOfferForm(instance=offer)
        formset = CheckoutBenefitFormSet(instance=offer)

    plans = PaymentPlan.objects.filter(course=offer.course).order_by("display_order", "name")
    checkout_url = request.build_absolute_uri(reverse("payments:checkout_start", args=[offer.course.slug]))

    return render(request, "courses/product_form.html", {
        "form": form, "formset": formset, "offer": offer, "title": f"Modifier {offer.title}", "is_create": False,
        "plans": plans, "checkout_url": checkout_url,
        "can_add_plan": request.user.has_perm("payments.add_paymentplan"),
        "can_edit_plan": request.user.has_perm("payments.change_paymentplan"),
    })
