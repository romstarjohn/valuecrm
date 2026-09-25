from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import DecimalField, OuterRef, Q, Subquery, Sum
from django.http import JsonResponse
from django.template.loader import render_to_string
from django.urls import reverse
from apps.configuration.services import ConfigurationService
from apps.courses.models import Course
from apps.enrollments.models import EnrollmentAttempt
from apps.enrollments.services import EnrollmentService
from apps.payments.models import AdminAuditLog, Installment, PaymentConfirmation
from apps.provisioning.models import ProvisioningRequest
from integrations.clickfunnels.client import ClickFunnelsClient
from .models import Contact
from .forms import ContactForm
from .services import ContactService

# Column -> model field(s) a list-page column may be sorted by. Whitelisted
# so `?sort=` can never reach arbitrary/relation fields.
CONTACT_SORT_FIELDS = {
    "name": ["first_name", "last_name"],
    "status": ["status"],
    "source": ["source"],
    "paid": ["total_paid_amount"],
    "added": ["created_at"],
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
    preserving every other active filter/sort param and resetting to page 1.
    `options` is either a flat list of values (label == value, e.g. status)
    or a list of (value, label) pairs (e.g. course_id -> course name)."""
    current = request.GET.get(param, "")
    base_params = request.GET.copy()
    base_params.pop("page", None)

    all_params = base_params.copy()
    all_params.pop(param, None)
    rows = [{"label": all_label, "url": "?" + all_params.urlencode(), "active": not current}]

    for option in options:
        value, label = option if isinstance(option, (tuple, list)) else (option, option)
        opt_params = base_params.copy()
        opt_params[param] = value
        rows.append({"label": label, "url": "?" + opt_params.urlencode(), "active": current == value})
    return rows

@login_required
def contact_list(request):
    """
    CRM List View for Contacts with search, filtering and sorting.
    """
    query = request.GET.get("q", "")
    status_filter = request.GET.get("status", "")
    course_filter = request.GET.get("course_id", "")

    # Correlated scalar subquery (not a joined annotation) — sums every PAID
    # installment across every one of the contact's orders without the
    # row-multiplication a direct Sum("orders__installments__...") join would
    # cause once combined with any other relation-based annotation.
    paid_total = (
        Installment.objects.filter(order__customer=OuterRef("pk"), status=Installment.Status.PAID)
        .values("order__customer")
        .annotate(total=Sum("paid_amount"))
        .values("total")
    )
    contacts = Contact.objects.all().annotate(
        total_paid_amount=Subquery(paid_total, output_field=DecimalField(max_digits=12, decimal_places=2)),
    )

    if query:
        contacts = contacts.filter(
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(email__icontains=query)
        )

    if status_filter:
        contacts = contacts.filter(status=status_filter)

    if course_filter:
        # Joined filter (not the Subquery annotations above) — a contact with
        # more than one enrollment attempt for this course would otherwise
        # repeat once per attempt.
        contacts = contacts.filter(enrollment_attempts__course__cf_course_id=course_filter).distinct()

    sort_key = request.GET.get("sort", "")
    sort_dir = request.GET.get("dir", "asc")
    if sort_key in CONTACT_SORT_FIELDS:
        order_fields = CONTACT_SORT_FIELDS[sort_key]
        if sort_dir == "desc":
            order_fields = [f"-{field}" for field in order_fields]
        contacts = contacts.order_by(*order_fields)
    else:
        sort_key = ""
        sort_dir = ""

    paginator = Paginator(contacts, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    # Options for filters. .order_by() clears the model's default ordering
    # (Contact.Meta.ordering = ["-created_at"]) before .distinct() — without
    # it, Django folds created_at into the SELECT DISTINCT so two contacts
    # sharing the same (blank) status never collapse into one row. Blank
    # status values are excluded outright — an empty-label dropdown entry
    # is never a usable filter option.
    status_options = (
        Contact.objects.exclude(status="").order_by().values_list("status", flat=True).distinct()
    )
    course_choices = [(c.cf_course_id, c.name) for c in Course.objects.order_by("name")]
    course_label = next((name for cf_id, name in course_choices if cf_id == course_filter), course_filter) if course_filter else "Tous"

    headers = [
        {"label": "Client", "sortable": True, "sort_key": "name"},
        {"label": "Tags", "sortable": False},
        {"label": "Statut (abonnement)", "sortable": True, "sort_key": "status"},
        {"label": "Montant total payé", "sortable": True, "sort_key": "paid", "align": "num"},
        {"label": "Ajouté le", "sortable": True, "sort_key": "added"},
        {"label": "Dernière mise à jour", "sortable": True, "sort_key": "updated"},
    ]

    context = {
        "page_obj": page_obj,
        "query": query,
        "status_filter": status_filter,
        "course_filter": course_filter,
        "status_options": status_options,
        "course_options": course_choices,
        "status_filter_options": _filter_options(request, param="status", options=status_options, all_label="Tous les statuts"),
        "course_filter_options": _filter_options(request, param="course_id", options=course_choices, all_label="Tous les cours"),
        "status_label": f"Statut : {status_filter or 'Tous'}",
        "course_label": f"Cours : {course_label}",
        "headers": headers,
        "sort_key": sort_key,
        "sort_dir": sort_dir,
        "sort_qs": _querystring(request, exclude=("page", "sort", "dir")),
        "querystring": _querystring(request, exclude=("page",)),
    }
    return render(request, "contacts/list.html", context)

_AUDIT_OUTCOME_PILL = {
    AdminAuditLog.OutcomeCategory.SUCCESS: "good",
    AdminAuditLog.OutcomeCategory.NO_OP_ALREADY_IN_STATE: "neutral",
    AdminAuditLog.OutcomeCategory.REJECTED_INVALID_STATE: "critical",
    AdminAuditLog.OutcomeCategory.REJECTED_PERMISSION: "critical",
    AdminAuditLog.OutcomeCategory.PROVIDER_NON_FINAL: "warn",
    AdminAuditLog.OutcomeCategory.FAILED: "critical",
}


def _build_contact_activity(contact, limit=50):
    """
    Merges every real, already-recorded trace of this contact's history into
    one chronological feed — never fabricates an event type the schema
    doesn't actually capture. Three genuine sources:
    - AdminAuditLog rows for orders belonging to this contact (suspensions,
      corrections, waivers, cancellations — the immutable admin trail).
    - PaymentConfirmation delivery outcomes (the emails we actually sent).
    - EnrollmentAttempt creation (each ClickFunnels enrollment call and its
      real result).
    """
    events = []

    audit_entries = (
        AdminAuditLog.objects.filter(order__customer=contact)
        .select_related("order", "installment")
        .order_by("-created_at")[:limit]
    )
    for entry in audit_entries:
        detail = entry.reason
        if entry.previous_state or entry.resulting_state:
            detail = f"{entry.previous_state or '—'} → {entry.resulting_state or '—'} · {entry.reason}"
        events.append({
            "date": entry.created_at,
            "icon": "fa-user-shield",
            "label": entry.get_action_type_display(),
            "detail": detail,
            "actor": entry.administrator_username or "système",
            "pill": _AUDIT_OUTCOME_PILL.get(entry.outcome_category, "neutral"),
            "pill_label": entry.get_outcome_category_display(),
        })

    for confirmation in contact.payment_confirmations.select_related("order")[:limit]:
        is_sent = confirmation.status == PaymentConfirmation.Status.SENT
        events.append({
            "date": confirmation.sent_at or confirmation.created_at,
            "icon": "fa-envelope",
            "label": "Confirmation de paiement",
            "detail": f"Commande {confirmation.order.reference}" if confirmation.order_id else "",
            "actor": "système",
            "pill": "good" if is_sent else ("critical" if confirmation.status == PaymentConfirmation.Status.FAILED else "warn"),
            "pill_label": confirmation.get_status_display(),
        })

    for enr in contact.enrollment_attempts.select_related("course")[:limit]:
        is_success = enr.status == "SUCCESS"
        events.append({
            "date": enr.created_at,
            "icon": "fa-graduation-cap",
            "label": "Tentative d'inscription",
            "detail": enr.course.name,
            "actor": "système",
            "pill": "good" if is_success else "critical",
            "pill_label": "Réussie" if is_success else "Échouée",
        })

    events.sort(key=lambda e: e["date"], reverse=True)
    return events[:limit]


@login_required
def contact_detail(request, pk):
    """
    Detailed contact profile view — tabbed: Aperçu, Activité, Commandes
    (orders and, separately, individual payments received), Inscriptions.
    """
    contact = get_object_or_404(Contact, pk=pk)
    enrollments = list(contact.enrollment_attempts.all().select_related("course"))
    orders = list(
        contact.orders.select_related("plan", "course").prefetch_related("installments").order_by("-created_at")
    )
    # Distinct from `orders` above: each individual amount actually received
    # (docs/PRODUCT_CADRAGE_PMI.md §4 "Paiement reçu" vs "Achat / commande" —
    # two different concepts, never collapsed into one row).
    payments = list(
        Installment.objects.filter(order__customer=contact, status=Installment.Status.PAID)
        .select_related("order")
        .order_by("-paid_at")
    )
    activity = _build_contact_activity(contact)

    # Link each enrollment row to its matching payments Order (if any), so
    # staff can jump to the operations hub for order-level actions (e.g.
    # Cancel Order) that have no equivalent here. Freeze/resume themselves
    # work directly off EnrollmentAttempt and don't need this lookup.
    order_refs = dict(
        ProvisioningRequest.objects.filter(
            contact_id=contact.pk, course_id__in=[e.course_id for e in enrollments],
        )
        .exclude(status=ProvisioningRequest.Status.CANCELLED)
        .values_list("course_id", "order__reference")
    )
    for enr in enrollments:
        enr.order_reference = order_refs.get(enr.course_id)

    context = {
        "contact": contact,
        "enrollments": enrollments,
        "orders": orders,
        "payments": payments,
        "activity": activity,
        "can_view_order": request.user.has_perm("payments.view_order"),
        "can_freeze_enrollment": request.user.has_perm("payments.freeze_enrollment"),
        "can_resume_enrollment": request.user.has_perm("payments.resume_enrollment"),
    }
    return render(request, "contacts/detail.html", context)

@login_required
def contact_detail_panel(request, pk):
    """
    JSON payload for the Contacts list's slide-over — same shell contract as
    apps.courses.views.course_detail_panel (static/js/slideover.js). Condensed
    version of contact_detail's data: enough to answer "who is this and what's
    their status" without leaving the list.
    """
    contact = get_object_or_404(Contact, pk=pk)
    orders = list(
        contact.orders.select_related("plan", "course").prefetch_related("installments").order_by("-created_at")[:5]
    )
    enrollments = list(contact.enrollment_attempts.select_related("course").order_by("-created_at")[:5])

    actions_html = (
        f'<a href="{reverse("contacts:edit", args=[contact.pk])}" class="btn-crm btn-crm-outline btn-crm-sm">'
        f'<i class="fa-solid fa-pen me-1 small" aria-hidden="true"></i>Modifier</a>'
    )

    body_html = render_to_string(
        "contacts/_detail_panel_body.html",
        {"contact": contact, "orders": orders, "enrollments": enrollments},
        request=request,
    )

    initials = f"{contact.first_name[:1]}{contact.last_name[:1]}".upper() or contact.email[:1].upper()
    return JsonResponse({
        "title": f"{contact.first_name} {contact.last_name}".strip() or contact.email,
        "subtitle": contact.email,
        "actions_html": actions_html,
        "body_html": body_html,
        "initials": initials,
    })


@login_required
def contact_create(request):
    """
    View to add a new contact.
    """
    if request.method == "POST":
        form = ContactForm(request.POST)
        if form.is_valid():
            contact = form.save()
            messages.success(request, f"Contact {contact.email} créé avec succès.")
            return redirect("contacts:detail", pk=contact.pk)
    else:
        form = ContactForm()

    return render(request, "contacts/form.html", {"form": form, "title": "Ajouter un contact"})

@login_required
def contact_update(request, pk):
    """
    View to edit an existing contact.
    """
    contact = get_object_or_404(Contact, pk=pk)
    if request.method == "POST":
        form = ContactForm(request.POST, instance=contact)
        if form.is_valid():
            contact = form.save()
            messages.success(request, f"Contact {contact.email} mis à jour avec succès.")
            return redirect("contacts:detail", pk=contact.pk)
    else:
        form = ContactForm(instance=contact)

    return render(request, "contacts/form.html", {"form": form, "contact": contact, "title": f"Modifier {contact.email}"})


@login_required
def contact_enroll_panel(request, pk):
    """
    JSON payload for the "Inscrire à un cours" slide-over — a course picker
    with checkboxes (multiple selection), reusing the same shell contract as
    contact_detail_panel/course_detail_panel. Courses the contact already has
    active (SUCCESS, not suspended) are left out — enrolling into something
    already open is not a real action to offer.
    """
    contact = get_object_or_404(Contact, pk=pk)
    already_active_course_ids = EnrollmentAttempt.objects.filter(
        contact=contact, status=EnrollmentAttempt.Status.SUCCESS, cf_suspended=False,
    ).values_list("course_id", flat=True)
    courses = Course.objects.exclude(pk__in=already_active_course_ids).order_by("name")

    body_html = render_to_string(
        "contacts/_enroll_panel_body.html", {"contact": contact, "courses": courses}, request=request,
    )
    return JsonResponse({
        "title": "Inscrire à un cours",
        "subtitle": f"{contact.first_name} {contact.last_name}".strip() or contact.email,
        "actions_html": "",
        "body_html": body_html,
    })


@login_required
def contact_enroll(request, pk):
    """
    Enrolls the contact into every course selected in the picker — one real
    EnrollmentService.enroll_contact() call per course (the same call
    enrollments:new makes for a single course), never a fabricated bulk
    endpoint. Each course's real result (success/failure) is reported back;
    a failure on one course never blocks the others.
    """
    contact = get_object_or_404(Contact, pk=pk)
    if request.method != "POST":
        return redirect("contacts:detail", pk=contact.pk)

    course_ids = request.POST.getlist("course_ids")
    if not course_ids:
        messages.error(request, "Sélectionnez au moins un cours à inscrire.")
        return redirect(f"{reverse('contacts:detail', args=[contact.pk])}#tab-enrollments")

    config = ConfigurationService().get_active_config()
    if not config:
        messages.error(request, "Aucune configuration ClickFunnels active trouvée.")
        return redirect(f"{reverse('contacts:detail', args=[contact.pk])}#tab-enrollments")
    if not config.workspace_id or not config.workspace_subdomain:
        messages.error(request, "Sélectionnez un espace de travail dans les paramètres avant d'inscrire des contacts.")
        return redirect("configuration:settings")

    client = ClickFunnelsClient.from_configuration(config)
    service = EnrollmentService(client=client, contact_service=ContactService(client=client))

    courses = Course.objects.filter(pk__in=course_ids)
    successes, failures = [], []
    for course in courses:
        try:
            result = service.enroll_contact(
                workspace_subdomain=config.workspace_subdomain, workspace_id=int(config.workspace_id),
                email=contact.email, cf_course_id=course.cf_course_id,
            )
        except Exception as e:
            failures.append(f"{course.name} ({e})")
            continue
        if result.status == EnrollmentAttempt.Status.SUCCESS:
            successes.append(course.name)
        else:
            failures.append(f"{course.name} ({result.error_message})")

    if successes:
        messages.success(request, f"Inscrit à : {', '.join(successes)}.")
    if failures:
        messages.error(request, f"Échec pour : {', '.join(failures)}.")

    return redirect(f"{reverse('contacts:detail', args=[contact.pk])}#tab-enrollments")
