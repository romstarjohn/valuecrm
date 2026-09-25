import logging

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.template.loader import render_to_string
from django.urls import reverse
from .models import EnrollmentAttempt
from .forms import EnrollmentForm, BulkEnrollmentForm
from .services import EnrollmentService
from apps.contacts.services import ContactService
from apps.configuration.services import ConfigurationService
from apps.courses.models import Course
from apps.operations.actions import render_confirmation_or_process
from apps.payments.admin_services import EnrollmentAdministrationService
from apps.provisioning.models import ProvisioningRequest
from integrations.clickfunnels.client import ClickFunnelsClient

logger = logging.getLogger(__name__)


ENROLLMENT_SORT_FIELDS = {
    "student": ["contact__email"],
    "course": ["course__name"],
    "status": ["status"],
    "date": ["created_at"],
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

    for value, label in options:
        opt_params = base_params.copy()
        opt_params[param] = value
        rows.append({"label": label, "url": "?" + opt_params.urlencode(), "active": current == value})
    return rows

def _attach_order_references(attempts):
    """
    Links each EnrollmentAttempt to its matching payments Order reference (if
    any). Freeze/resume work directly on any EnrollmentAttempt regardless —
    this is only used to offer an extra "View Order" link into the
    operations hub for order-level actions (e.g. Cancel Order) that have no
    equivalent here. Batched to avoid N+1s.
    """
    attempts = list(attempts)
    rows = (
        ProvisioningRequest.objects.filter(
            contact_id__in=[a.contact_id for a in attempts],
            course_id__in=[a.course_id for a in attempts],
        )
        .exclude(status=ProvisioningRequest.Status.CANCELLED)
        .values_list("contact_id", "course_id", "order__reference")
    )
    order_refs = {(contact_id, course_id): reference for contact_id, course_id, reference in rows}
    for attempt in attempts:
        attempt.order_reference = order_refs.get((attempt.contact_id, attempt.course_id))
    return attempts

def _get_enrollment_service():
    config_service = ConfigurationService()
    config = config_service.get_active_config()
    if not config:
        return None, None
    
    client = ClickFunnelsClient.from_configuration(config)
    contact_service = ContactService(client=client)
    return EnrollmentService(client=client, contact_service=contact_service), config

@login_required
def enrollment_list(request):
    """
    CRM List View for Enrollment History.
    """
    query = request.GET.get("q", "")
    status_filter = request.GET.get("status", "")
    course_filter = request.GET.get("course_id", "")
    
    attempts = EnrollmentAttempt.objects.all().select_related("contact", "course")
    
    if query:
        attempts = attempts.filter(
            Q(contact__email__icontains=query)
            | Q(contact__first_name__icontains=query)
            | Q(contact__last_name__icontains=query)
            | Q(course__name__icontains=query)
        )
    
    if status_filter:
        attempts = attempts.filter(status=status_filter)
    
    if course_filter:
        attempts = attempts.filter(course__cf_course_id=course_filter)

    sort_key = request.GET.get("sort", "")
    sort_dir = request.GET.get("dir", "asc")
    if sort_key in ENROLLMENT_SORT_FIELDS:
        order_fields = ENROLLMENT_SORT_FIELDS[sort_key]
        if sort_dir == "desc":
            order_fields = [f"-{field}" for field in order_fields]
        attempts = attempts.order_by(*order_fields)
    else:
        sort_key = ""
        sort_dir = ""

    paginator = Paginator(attempts, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    page_obj.object_list = _attach_order_references(page_obj.object_list)

    status_labels_fr = {"SUCCESS": "Accès ouvert", "FAILURE": "Échec"}
    status_options = [(value, status_labels_fr.get(value, label)) for value, label in EnrollmentAttempt.Status.choices]
    course_options = Course.objects.all().order_by("name")
    course_choices = [(c.cf_course_id, c.name) for c in course_options]
    course_label = next((name for cf_id, name in course_choices if cf_id == course_filter), course_filter) if course_filter else "Toutes"

    headers = [
        {"label": "Client", "sortable": True, "sort_key": "student"},
        {"label": "Formation", "sortable": True, "sort_key": "course"},
        {"label": "Accès", "sortable": True, "sort_key": "status"},
        {"label": "Depuis", "sortable": True, "sort_key": "date"},
        {"label": "", "sortable": False, "align": "text-end"},
    ]

    context = {
        "page_obj": page_obj,
        "query": query,
        "status_filter": status_filter,
        "course_filter": course_filter,
        "status_options": status_options,
        "course_options": course_options,
        "status_filter_options": _filter_options(request, param="status", options=status_options, all_label="Tous les accès"),
        "course_filter_options": _filter_options(request, param="course_id", options=course_choices, all_label="Toutes les formations"),
        "status_label": f"Accès : {dict(status_options).get(status_filter, 'Tous')}",
        "course_label": f"Formation : {course_label}",
        "headers": headers,
        "sort_key": sort_key,
        "sort_dir": sort_dir,
        "sort_qs": _querystring(request, exclude=("page", "sort", "dir")),
        "querystring": _querystring(request, exclude=("page",)),
        "can_view_order": request.user.has_perm("payments.view_order"),
        "can_freeze_enrollment": request.user.has_perm("payments.freeze_enrollment"),
        "can_resume_enrollment": request.user.has_perm("payments.resume_enrollment"),
    }
    return render(request, "enrollments/list.html", context)

@login_required
def enrollment_new(request):
    """
    Single enrollment trigger view.
    """
    service, config = _get_enrollment_service()
    
    if request.method == "POST":
        form = EnrollmentForm(request.POST)
        if form.is_valid():
            if not service:
                messages.error(request, "ClickFunnels n'est pas encore connecté. Connectez-le dans Réglages → Connexion ClickFunnels.")
                return redirect("enrollments:new")

            email = form.cleaned_data["email"]
            course_id = form.cleaned_data["course_id"]

            # Validation
            if not config.workspace_id or not config.workspace_subdomain:
                messages.error(request, "Choisissez d'abord votre espace de travail ClickFunnels sur la page Connexion ClickFunnels.")
                return redirect("configuration:settings")

            try:
                result_dto = service.enroll_contact(
                    workspace_subdomain=config.workspace_subdomain,
                    workspace_id=int(config.workspace_id),
                    email=email,
                    cf_course_id=course_id
                )
                if result_dto.status == EnrollmentAttempt.Status.SUCCESS:
                    messages.success(request, f"Accès ouvert pour {email}.")
                    return redirect("enrollments:detail", pk=result_dto.enrollment_attempt_id)
                else:
                    messages.error(request, f"L'accès n'a pas pu être ouvert pour {email}.")
                    if result_dto.enrollment_attempt_id:
                        return redirect("enrollments:detail", pk=result_dto.enrollment_attempt_id)
            except Exception:
                logger.exception("Unexpected error while granting course access")
                messages.error(request, "Une erreur inattendue est survenue. Réessayez ; si cela se reproduit, contactez le support technique.")
                
    else:
        # Pre-fill from query params if available (e.g. from contact detail)
        initial = {
            "email": request.GET.get("email", ""),
            "course_id": request.GET.get("course_id", "")
        }
        form = EnrollmentForm(initial=initial)
        
    return render(request, "enrollments/single.html", {"form": form, "config": config})

@login_required
def enrollment_bulk(request):
    """
    Bulk enrollment view with result summary.
    """
    service, config = _get_enrollment_service()
    bulk_result = None
    
    if request.method == "POST":
        form = BulkEnrollmentForm(request.POST)
        if form.is_valid():
            if not service:
                messages.error(request, "ClickFunnels n'est pas encore connecté. Connectez-le dans Réglages → Connexion ClickFunnels.")
                return redirect("enrollments:bulk")

            emails = form.cleaned_data["emails"]
            course_id = form.cleaned_data["course_id"]

            # Validation
            if not config.workspace_id or not config.workspace_subdomain:
                messages.error(request, "Choisissez d'abord votre espace de travail ClickFunnels sur la page Connexion ClickFunnels.")
                return redirect("configuration:settings")

            try:
                bulk_result = service.bulk_enroll(
                    workspace_subdomain=config.workspace_subdomain,
                    workspace_id=int(config.workspace_id),
                    emails=emails,
                    cf_course_id=course_id
                )
                messages.info(request, f"{bulk_result['success_count']} accès ouvert(s), {bulk_result['failure_count']} échec(s).")
            except Exception:
                logger.exception("Unexpected error while granting course access")
                messages.error(request, "Une erreur inattendue est survenue. Réessayez ; si cela se reproduit, contactez le support technique.")
    else:
        form = BulkEnrollmentForm()
        
    context = {
        "form": form,
        "config": config,
        "bulk_result": bulk_result
    }
    return render(request, "enrollments/bulk.html", context)

@login_required
def enrollment_detail(request, pk):
    """
    Detailed audit view for an enrollment attempt.
    """
    attempt = get_object_or_404(EnrollmentAttempt.objects.select_related("contact", "course"), pk=pk)
    order_reference = (
        ProvisioningRequest.objects.filter(contact_id=attempt.contact_id, course_id=attempt.course_id)
        .exclude(status=ProvisioningRequest.Status.CANCELLED)
        .values_list("order__reference", flat=True)
        .first()
    )

    context = {
        "attempt": attempt,
        "order_reference": order_reference,
        "can_view_order": request.user.has_perm("payments.view_order"),
        "can_freeze_enrollment": request.user.has_perm("payments.freeze_enrollment"),
        "can_resume_enrollment": request.user.has_perm("payments.resume_enrollment"),
    }
    return render(request, "enrollments/detail.html", context)


@login_required
def enrollment_detail_panel(request, pk):
    """
    JSON payload for the Inscriptions list's slide-over — same shell contract
    as contact/course detail_panel views.
    """
    attempt = get_object_or_404(EnrollmentAttempt.objects.select_related("contact", "course"), pk=pk)
    order_reference = (
        ProvisioningRequest.objects.filter(contact_id=attempt.contact_id, course_id=attempt.course_id)
        .exclude(status=ProvisioningRequest.Status.CANCELLED)
        .values_list("order__reference", flat=True)
        .first()
    )
    body_html = render_to_string(
        "enrollments/_detail_panel_body.html",
        {
            "attempt": attempt, "order_reference": order_reference,
            "can_view_order": request.user.has_perm("payments.view_order"),
            "can_freeze_enrollment": request.user.has_perm("payments.freeze_enrollment"),
            "can_resume_enrollment": request.user.has_perm("payments.resume_enrollment"),
        },
        request=request,
    )
    return JsonResponse({
        "title": f"{attempt.contact.first_name} {attempt.contact.last_name}".strip() or attempt.contact.email,
        "subtitle": attempt.course.name,
        "actions_html": "",
        "body_html": body_html,
    })


@login_required
def enrollment_freeze(request, pk):
    attempt = get_object_or_404(EnrollmentAttempt.objects.select_related("contact", "course"), pk=pk)

    def do_freeze(reason):
        EnrollmentAdministrationService().freeze_enrollment_attempt(attempt.pk, reason, request.user, request)
        return f"Accès suspendu pour {attempt.contact.email} à « {attempt.course.name} »."

    context = {
        "title": "Suspendre l'accès",
        "submit_label": "Suspendre l'accès",
        "reason_placeholder": "Ex. : paiement en retard, demande du client…",
        "description": "Le client voit toujours la formation, mais ne peut plus suivre les leçons. Vous pourrez rétablir l'accès à tout moment.",
        "target_label": f"{attempt.contact.email} — {attempt.course.name}",
        "back_url": reverse("enrollments:detail", args=[attempt.pk]),
    }
    response = render_confirmation_or_process(
        request, permission="payments.freeze_enrollment", context=context, service_call=do_freeze,
    )
    if response is not None:
        return response
    return redirect("enrollments:detail", pk=attempt.pk)


@login_required
def enrollment_resume(request, pk):
    attempt = get_object_or_404(EnrollmentAttempt.objects.select_related("contact", "course"), pk=pk)

    def do_resume(reason):
        EnrollmentAdministrationService().resume_enrollment_attempt(attempt.pk, reason, request.user, request)
        return f"Accès rétabli pour {attempt.contact.email} à « {attempt.course.name} »."

    context = {
        "title": "Rétablir l'accès",
        "submit_label": "Rétablir l'accès",
        "reason_placeholder": "Ex. : paiement reçu, erreur corrigée…",
        "description": "Le client peut de nouveau suivre toutes les leçons de la formation.",
        "target_label": f"{attempt.contact.email} — {attempt.course.name}",
        "back_url": reverse("enrollments:detail", args=[attempt.pk]),
    }
    response = render_confirmation_or_process(
        request, permission="payments.resume_enrollment", context=context, service_call=do_resume,
    )
    if response is not None:
        return response
    return redirect("enrollments:detail", pk=attempt.pk)
