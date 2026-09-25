"""
Payment & Enrollment Operations dashboard area. Mirrors the existing CRUD
list/detail pattern established by apps/contacts/views.py and
apps/courses/views.py (plain @login_required FBVs, django.db.models.Q
filters built from validated request.GET values, django.core.paginator.
Paginator, a `headers` list of dicts passed to templates/components/
table_start.html). List/detail pages additionally enforce Django's
auto-generated per-model `view_<model>` permission server-side (Phase 8
established the explicit `has_perm()` + `raise PermissionDenied` convention
for sensitive checks — see apps/payments/admin_actions.py).

Never duplicates business logic: every computed eligibility/progress figure
mirrors the exact read-only rules already established in
apps/payments/admin.py::OrderAdmin (Phase 8) — computed from prefetched
querysets, not by re-deriving new rules.
"""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_date

from apps.payments.admin_services import (
    AdminActionError,
    EnrollmentAdministrationService,
    OrderAdministrationService,
    PaymentAttemptAdministrationService,
    PaymentConfirmationAdministrationService,
    ReconciliationAdministrationService,
)
from apps.payments.models import (
    AdminAuditLog,
    Installment,
    Order,
    PaymentAttempt,
    PaymentConfirmation,
    PaymentPlan,
    ReconciliationRun,
    TaraWebhookEvent,
)
from apps.provisioning.models import ProvisioningAttempt, ProvisioningRequest
from apps.provisioning.services import ProvisioningService

from .actions import render_confirmation_or_process

def _querystring_without_page(request) -> str:
    """
    Preserves every current filter param across pagination/sorting links —
    templates/components/pagination.html falls back to its original q-only
    behavior when this isn't provided, so existing callers (contacts/courses)
    are unaffected.
    """
    params = request.GET.copy()
    params.pop("page", None)
    return params.urlencode()


_ORDER_HEADERS = [
    {"label": "Commande", "sortable": False},
    {"label": "Client", "sortable": False},
    {"label": "Plan / Formation", "sortable": False},
    {"label": "Finances", "sortable": False},
    {"label": "Progression", "sortable": False},
    {"label": "Statut", "sortable": True},
    {"label": "Créée le", "sortable": True},
]


@login_required
def hub(request):
    """
    Operations landing page — server-derived summary counts, each linking to
    the corresponding filtered list per resource (only where that list route
    already exists), plus a workflow overview using existing card/step
    markup (no new visual system).
    """
    today = date.today()

    active_plans = PaymentPlan.objects.filter(is_active=True).count()
    pending_orders = Order.objects.filter(status=Order.Status.PENDING).count()
    payments_awaiting_verification = PaymentAttempt.objects.filter(
        status__in=[PaymentAttempt.Status.PENDING, PaymentAttempt.Status.UNKNOWN],
    ).count()
    due_installments = Installment.objects.filter(
        status__in=[Installment.Status.SCHEDULED, Installment.Status.DUE, Installment.Status.PENDING],
        due_date__lte=today,
    ).count()
    confirmations_awaiting_delivery = PaymentConfirmation.objects.filter(
        status__in=[PaymentConfirmation.Status.PENDING, PaymentConfirmation.Status.FAILED],
    ).count()
    enrollments_awaiting_processing = ProvisioningRequest.objects.filter(
        status__in=[ProvisioningRequest.Status.PENDING, ProvisioningRequest.Status.FAILED],
    ).count()
    manual_review_items = (
        PaymentAttempt.objects.filter(status=PaymentAttempt.Status.UNKNOWN).count()
        + PaymentConfirmation.objects.filter(status=PaymentConfirmation.Status.MANUAL_REVIEW).count()
        + ProvisioningRequest.objects.filter(status=ProvisioningRequest.Status.MANUAL_REVIEW).count()
    )
    failed_reconciliation_runs = ReconciliationRun.objects.filter(
        run_status__in=[ReconciliationRun.RunStatus.FAILED, ReconciliationRun.RunStatus.PARTIAL_FAILURE],
    ).count()
    recent_verified_payments = (
        PaymentAttempt.objects.filter(status=PaymentAttempt.Status.SUCCEEDED)
        .select_related("installment__order__customer")
        .order_by("-updated_at")[:5]
    )

    context = {
        "active_plans": active_plans,
        "pending_orders": pending_orders,
        "payments_awaiting_verification": payments_awaiting_verification,
        "due_installments": due_installments,
        "confirmations_awaiting_delivery": confirmations_awaiting_delivery,
        "enrollments_awaiting_processing": enrollments_awaiting_processing,
        "manual_review_items": manual_review_items,
        "failed_reconciliation_runs": failed_reconciliation_runs,
        "recent_verified_payments": recent_verified_payments,
    }
    return render(request, "operations/hub.html", context)


_ORDER_ELIGIBLE_Q = (
    Q(access_policy=PaymentPlan.AccessPolicy.FIRST_INSTALLMENT, _paid_count__gte=1)
    | Q(access_policy=PaymentPlan.AccessPolicy.FULL_PAYMENT, _installment_count__gt=0, _unsettled_count=0)
)


@login_required
def order_list(request):
    """Server-side search/filter/sort/pagination — mirrors apps/contacts/views.py::contact_list's shape."""
    if not request.user.has_perm("payments.view_order"):
        raise PermissionDenied

    query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "")
    plan_filter = request.GET.get("plan", "")
    course_filter = request.GET.get("course", "")
    access_filter = request.GET.get("access", "")
    disposition_filter = request.GET.get("disposition", "")
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    needs_review = request.GET.get("needs_review", "")

    orders = Order.objects.select_related("customer", "plan", "course").prefetch_related(
        "installments", "payment_confirmations", "provisioning_requests",
    )

    if query:
        orders = orders.filter(
            Q(reference__icontains=query)
            | Q(customer__first_name__icontains=query)
            | Q(customer__last_name__icontains=query)
            | Q(customer__email__icontains=query)
            | Q(plan_name__icontains=query)
            | Q(course_name__icontains=query)
        )

    if status_filter in dict(Order.Status.choices):
        orders = orders.filter(status=status_filter)

    if plan_filter.isdigit():
        orders = orders.filter(plan_id=int(plan_filter))

    if course_filter.isdigit():
        orders = orders.filter(course_id=int(course_filter))

    if disposition_filter in dict(Order.ManualDisposition.choices):
        orders = orders.filter(manual_disposition=disposition_filter)

    parsed_from = parse_date(date_from) if date_from else None
    if parsed_from:
        orders = orders.filter(created_at__date__gte=parsed_from)

    parsed_to = parse_date(date_to) if date_to else None
    if parsed_to:
        orders = orders.filter(created_at__date__lte=parsed_to)

    if access_filter in ("eligible", "not_eligible"):
        orders = orders.annotate(
            _paid_count=Count("installments", filter=Q(installments__status=Installment.Status.PAID), distinct=True),
            _unsettled_count=Count(
                "installments",
                filter=~Q(installments__status__in=[Installment.Status.PAID, Installment.Status.WAIVED]),
                distinct=True,
            ),
            _installment_count=Count("installments", distinct=True),
        )
        if access_filter == "eligible":
            orders = orders.filter(_ORDER_ELIGIBLE_Q)
        else:
            orders = orders.exclude(_ORDER_ELIGIBLE_Q)

    if needs_review == "1":
        orders = orders.filter(
            Q(installments__status=Installment.Status.FAILED)
            | Q(installments__payment_attempts__status__in=[PaymentAttempt.Status.PENDING, PaymentAttempt.Status.UNKNOWN])
            | Q(payment_confirmations__status=PaymentConfirmation.Status.MANUAL_REVIEW)
            | Q(provisioning_requests__status=ProvisioningRequest.Status.MANUAL_REVIEW)
        ).distinct()

    orders = orders.order_by("-created_at")

    paginator = Paginator(orders, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    for order in page_obj:
        _attach_order_display_fields(order)

    context = {
        "page_obj": page_obj,
        "headers": _ORDER_HEADERS,
        "query": query,
        "status_filter": status_filter,
        "plan_filter": plan_filter,
        "course_filter": course_filter,
        "access_filter": access_filter,
        "disposition_filter": disposition_filter,
        "date_from": date_from,
        "date_to": date_to,
        "needs_review": needs_review,
        "status_options": Order.Status.choices,
        "disposition_options": Order.ManualDisposition.choices,
        "plan_options": PaymentPlan.objects.order_by("name").values_list("id", "name"),
        "querystring": _querystring_without_page(request),
    }
    return render(request, "operations/order_list.html", context)


def _attach_order_display_fields(order: Order) -> None:
    """
    Same N+1-safe computation as apps/payments/admin.py::OrderAdmin's display
    methods (Phase 8) — reads the prefetched `installments`/
    `payment_confirmations`/`provisioning_requests` lists in Python instead
    of re-querying Order.total_paid_amount/is_access_eligible.
    """
    installments = list(order.installments.all())
    paid_total = sum((i.paid_amount or 0) for i in installments if i.status == Installment.Status.PAID)
    order.display_total_paid = paid_total
    order.display_remaining_balance = order.total_expected_amount - paid_total
    order.display_paid_count = sum(1 for i in installments if i.status == Installment.Status.PAID)

    if order.access_policy == PaymentPlan.AccessPolicy.FIRST_INSTALLMENT:
        order.display_access_eligible = any(i.status == Installment.Status.PAID for i in installments)
    elif order.access_policy == PaymentPlan.AccessPolicy.FULL_PAYMENT:
        order.display_access_eligible = bool(installments) and all(
            i.status in (Installment.Status.PAID, Installment.Status.WAIVED) for i in installments
        )
    else:
        order.display_access_eligible = False

    confirmation_statuses = {c.status for c in order.payment_confirmations.all()}
    order.display_confirmation_state = ", ".join(sorted(confirmation_statuses)) if confirmation_statuses else "—"

    provisioning_statuses = {r.status for r in order.provisioning_requests.all()}
    order.display_provisioning_state = ", ".join(sorted(provisioning_statuses)) if provisioning_statuses else "—"


@login_required
def order_detail(request, reference):
    """
    Single operational page for one customer purchase — customer info,
    purchased plan/course snapshot, financial summary, installments, payment
    attempts, correlated webhook events, confirmations, provisioning, and
    administrative audit history. Read-only; sensitive actions are separate
    confirmed-action views (Slice 2), never triggered from this page's GET.
    """
    if not request.user.has_perm("payments.view_order"):
        raise PermissionDenied

    order = get_object_or_404(
        Order.objects.select_related("customer", "plan", "course"), reference=reference,
    )
    installments = list(
        order.installments.all().prefetch_related("payment_attempts").order_by("sequence")
    )
    attempt_ids = [a.id for i in installments for a in i.payment_attempts.all()]
    webhook_events = (
        TaraWebhookEvent.objects.filter(payment_attempt_id__in=attempt_ids)
        .select_related("payment_attempt")
        .order_by("-received_at")
    )
    confirmations = order.payment_confirmations.all().order_by("-created_at")
    provisioning_requests = order.provisioning_requests.all().order_by("-created_at")
    audit_events = order.admin_audit_logs.all().order_by("-created_at")

    # Suspension state lives on EnrollmentAttempt (Phase 9), not
    # ProvisioningRequest — annotate each row with its latest successful
    # attempt's suspended flag so the template can show it without a
    # per-row query. Iterating (rather than list()) keeps this a QuerySet
    # with its results cached, so callers relying on .count()/further
    # queryset behavior on provisioning_requests still work.
    for pr in provisioning_requests:
        latest_success = (
            pr.attempts.filter(status=ProvisioningAttempt.Status.SUCCESS, enrollment_attempt__isnull=False)
            .select_related("enrollment_attempt")
            .order_by("-created_at")
            .first()
        )
        pr.cf_suspended = bool(latest_success and latest_success.enrollment_attempt.cf_suspended)

    _attach_order_display_fields(order)

    context = {
        "order": order,
        "installments": installments,
        "webhook_events": webhook_events,
        "confirmations": confirmations,
        "provisioning_requests": provisioning_requests,
        "audit_events": audit_events,
        "can_cancel_order": request.user.has_perm("payments.cancel_order"),
        "can_apply_disposition": request.user.has_perm("payments.apply_manual_disposition"),
        "can_freeze_enrollment": request.user.has_perm("payments.freeze_enrollment"),
        "can_resume_enrollment": request.user.has_perm("payments.resume_enrollment"),
        "can_cancel_installment": request.user.has_perm("payments.cancel_installment"),
        "can_waive_installment": request.user.has_perm("payments.waive_installment"),
        "can_check_tara_status": request.user.has_perm("payments.check_tara_status"),
        "can_retry_confirmation": request.user.has_perm("payments.retry_payment_confirmation"),
        "can_retry_provisioning": request.user.has_perm("provisioning.retry_provisioning_request"),
    }
    return render(request, "operations/order_detail.html", context)


# --- Installment list ---

_INSTALLMENT_HEADERS = [
    {"label": "Commande", "sortable": False}, {"label": "Client", "sortable": False},
    {"label": "N°", "sortable": False}, {"label": "Échéance", "sortable": True},
    {"label": "Attendu", "sortable": False}, {"label": "Payé", "sortable": False},
    {"label": "Statut", "sortable": True}, {"label": "Vérification", "sortable": False},
]


@login_required
def installment_list(request):
    if not request.user.has_perm("payments.view_installment"):
        raise PermissionDenied

    status_filter = request.GET.get("status", "")
    plan_filter = request.GET.get("plan", "")
    course_filter = request.GET.get("course", "")
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    overdue_only = request.GET.get("overdue", "")
    needs_review = request.GET.get("needs_review", "")

    installments = Installment.objects.select_related("order", "order__customer", "order__plan", "order__course").prefetch_related("payment_attempts")

    if status_filter in dict(Installment.Status.choices):
        installments = installments.filter(status=status_filter)
    if plan_filter.isdigit():
        installments = installments.filter(order__plan_id=int(plan_filter))
    if course_filter.isdigit():
        installments = installments.filter(order__course_id=int(course_filter))
    parsed_from = parse_date(date_from) if date_from else None
    if parsed_from:
        installments = installments.filter(due_date__gte=parsed_from)
    parsed_to = parse_date(date_to) if date_to else None
    if parsed_to:
        installments = installments.filter(due_date__lte=parsed_to)
    if overdue_only == "1":
        installments = installments.filter(
            due_date__lt=date.today(),
            status__in=[Installment.Status.SCHEDULED, Installment.Status.DUE, Installment.Status.PENDING],
        )
    if needs_review == "1":
        installments = installments.filter(
            Q(status=Installment.Status.FAILED)
            | Q(payment_attempts__status__in=[PaymentAttempt.Status.PENDING, PaymentAttempt.Status.UNKNOWN])
        ).distinct()

    installments = installments.order_by("-due_date")
    paginator = Paginator(installments, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        "page_obj": page_obj, "headers": _INSTALLMENT_HEADERS,
        "status_filter": status_filter, "plan_filter": plan_filter, "course_filter": course_filter,
        "date_from": date_from, "date_to": date_to, "overdue_only": overdue_only, "needs_review": needs_review,
        "status_options": Installment.Status.choices,
        "plan_options": PaymentPlan.objects.order_by("name").values_list("id", "name"),
        "querystring": _querystring_without_page(request),
    }
    return render(request, "operations/installment_list.html", context)


# --- Payment attempt list ---

_ATTEMPT_HEADERS = [
    {"label": "Statut interne", "sortable": True}, {"label": "Statut fournisseur", "sortable": False},
    {"label": "Tara Product ID", "sortable": False}, {"label": "Tara Payment ID", "sortable": False},
    {"label": "Attendu", "sortable": False}, {"label": "Commande / Versement", "sortable": False},
    {"label": "Mis à jour", "sortable": True},
]


@login_required
def payment_attempt_list(request):
    if not request.user.has_perm("payments.view_paymentattempt"):
        raise PermissionDenied

    status_filter = request.GET.get("status", "")
    provider_status_filter = request.GET.get("provider_status", "").strip()
    pending_unknown_only = request.GET.get("pending_unknown", "")
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    plan_filter = request.GET.get("plan", "")
    course_filter = request.GET.get("course", "")

    attempts = PaymentAttempt.objects.select_related("installment__order__customer", "installment__order__plan", "installment__order__course")

    if status_filter in dict(PaymentAttempt.Status.choices):
        attempts = attempts.filter(status=status_filter)
    if provider_status_filter:
        attempts = attempts.filter(raw_provider_status__icontains=provider_status_filter)
    if pending_unknown_only == "1":
        attempts = attempts.filter(status__in=[PaymentAttempt.Status.PENDING, PaymentAttempt.Status.UNKNOWN])
    parsed_from = parse_date(date_from) if date_from else None
    if parsed_from:
        attempts = attempts.filter(created_at__date__gte=parsed_from)
    parsed_to = parse_date(date_to) if date_to else None
    if parsed_to:
        attempts = attempts.filter(created_at__date__lte=parsed_to)
    if plan_filter.isdigit():
        attempts = attempts.filter(installment__order__plan_id=int(plan_filter))
    if course_filter.isdigit():
        attempts = attempts.filter(installment__order__course_id=int(course_filter))

    attempts = attempts.order_by("-updated_at")
    paginator = Paginator(attempts, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        "page_obj": page_obj, "headers": _ATTEMPT_HEADERS,
        "status_filter": status_filter, "provider_status_filter": provider_status_filter,
        "pending_unknown_only": pending_unknown_only, "date_from": date_from, "date_to": date_to,
        "plan_filter": plan_filter, "course_filter": course_filter,
        "status_options": PaymentAttempt.Status.choices,
        "plan_options": PaymentPlan.objects.order_by("name").values_list("id", "name"),
        "can_check_tara_status": request.user.has_perm("payments.check_tara_status"),
        "querystring": _querystring_without_page(request),
    }
    return render(request, "operations/payment_attempt_list.html", context)


# --- Webhook events (read-only) ---

_WEBHOOK_HEADERS = [
    {"label": "Référence", "sortable": False}, {"label": "Montant", "sortable": False},
    {"label": "Corrélation", "sortable": False},
    {"label": "Vérification", "sortable": False}, {"label": "Traitement", "sortable": True},
    {"label": "Catégorie d'échec", "sortable": False}, {"label": "Reçu", "sortable": True},
]


@login_required
def webhook_event_list(request):
    if not request.user.has_perm("payments.view_tarawebhookevent"):
        raise PermissionDenied

    processing_status_filter = request.GET.get("processing_status", "")
    verification_result_filter = request.GET.get("verification_result", "")
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")

    events = TaraWebhookEvent.objects.select_related("payment_attempt__installment__order")

    if processing_status_filter in dict(TaraWebhookEvent.ProcessingStatus.choices):
        events = events.filter(processing_status=processing_status_filter)
    if verification_result_filter in dict(TaraWebhookEvent.VerificationResult.choices):
        events = events.filter(verification_result=verification_result_filter)
    parsed_from = parse_date(date_from) if date_from else None
    if parsed_from:
        events = events.filter(received_at__date__gte=parsed_from)
    parsed_to = parse_date(date_to) if date_to else None
    if parsed_to:
        events = events.filter(received_at__date__lte=parsed_to)

    events = events.order_by("-received_at")
    paginator = Paginator(events, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        "page_obj": page_obj, "headers": _WEBHOOK_HEADERS,
        "processing_status_filter": processing_status_filter, "verification_result_filter": verification_result_filter,
        "date_from": date_from, "date_to": date_to,
        "processing_status_options": TaraWebhookEvent.ProcessingStatus.choices,
        "verification_result_options": TaraWebhookEvent.VerificationResult.choices,
        "can_attribute_payment": request.user.has_perm("payments.attribute_webhook_payment"),
        "querystring": _querystring_without_page(request),
    }
    return render(request, "operations/webhook_event_list.html", context)


_ATTRIBUTION_ELIGIBLE_INSTALLMENT_STATUSES = [
    Installment.Status.SCHEDULED, Installment.Status.DUE, Installment.Status.PENDING, Installment.Status.FAILED,
]


@login_required
def webhook_event_attribute(request, pk):
    """
    Administrator-only: manually associate a payment Tara confirmed but that
    could not be matched to a PaymentAttempt (UNCORRELATED — most often a
    payment made directly in the Tara app, not through our checkout link) to
    the correct order's installment. Reserved to the administrator role per
    docs/PRODUCT_CADRAGE_PMI.md §3 — never extended to gestionnaires.
    """
    if not request.user.has_perm("payments.attribute_webhook_payment"):
        raise PermissionDenied

    event = get_object_or_404(TaraWebhookEvent, pk=pk)
    if event.processing_status != TaraWebhookEvent.ProcessingStatus.UNCORRELATED:
        messages.error(request, "Cet événement n'est plus en attente de rapprochement.")
        return redirect("operations:webhook_event_list")

    query = request.GET.get("q", "").strip()
    candidates = []
    if query:
        candidates = list(
            Installment.objects.filter(status__in=_ATTRIBUTION_ELIGIBLE_INSTALLMENT_STATUSES)
            .filter(
                Q(order__reference__icontains=query)
                | Q(order__customer__first_name__icontains=query)
                | Q(order__customer__last_name__icontains=query)
                | Q(order__customer__email__icontains=query)
                | Q(order__customer__phone__icontains=query)
            )
            .select_related("order", "order__customer")
            .order_by("due_date")[:20]
        )

    if request.method == "POST":
        installment_id = request.POST.get("installment_id", "")
        reason = request.POST.get("reason", "").strip()
        confirmed_amount_raw = request.POST.get("confirmed_amount", "").strip()

        error = None
        confirmed_amount = None
        if not installment_id.isdigit():
            error = "Sélectionnez le versement concerné."
        elif not reason:
            error = "Un motif est requis pour cette action."
        else:
            try:
                confirmed_amount = Decimal(confirmed_amount_raw)
                if confirmed_amount <= 0:
                    raise InvalidOperation("must be positive")
            except InvalidOperation:
                error = "Montant confirmé invalide."

        if error:
            messages.error(request, error)
        else:
            try:
                ReconciliationAdministrationService().attribute_webhook_event(
                    event.pk, int(installment_id), confirmed_amount, reason, request.user, request,
                )
            except AdminActionError as e:
                messages.error(request, str(e))
            else:
                messages.success(request, "Paiement associé — le versement a été crédité du montant confirmé.")
                return redirect("operations:webhook_event_list")

    context = {"event": event, "query": query, "candidates": candidates}
    return render(request, "operations/webhook_event_attribute.html", context)


# --- Payment confirmations ---

_CONFIRMATION_HEADERS = [
    {"label": "Commande / Client", "sortable": False}, {"label": "Versement", "sortable": False},
    {"label": "Canal", "sortable": False}, {"label": "Statut", "sortable": True},
    {"label": "Tentatives", "sortable": False}, {"label": "Prochain essai", "sortable": False},
    {"label": "Envoyé", "sortable": True},
]


@login_required
def confirmation_list(request):
    if not request.user.has_perm("payments.view_paymentconfirmation"):
        raise PermissionDenied

    status_filter = request.GET.get("status", "")
    channel_filter = request.GET.get("channel", "")

    confirmations = PaymentConfirmation.objects.select_related("order__customer", "installment")

    if status_filter in dict(PaymentConfirmation.Status.choices):
        confirmations = confirmations.filter(status=status_filter)
    if channel_filter in dict(PaymentConfirmation.Channel.choices):
        confirmations = confirmations.filter(channel=channel_filter)

    confirmations = confirmations.order_by("-created_at")
    paginator = Paginator(confirmations, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        "page_obj": page_obj, "headers": _CONFIRMATION_HEADERS,
        "status_filter": status_filter, "channel_filter": channel_filter,
        "status_options": PaymentConfirmation.Status.choices,
        "channel_options": PaymentConfirmation.Channel.choices,
        "can_retry_confirmation": request.user.has_perm("payments.retry_payment_confirmation"),
        "querystring": _querystring_without_page(request),
    }
    return render(request, "operations/confirmation_list.html", context)


# --- Provisioning / enrollment ---

_PROVISIONING_HEADERS = [
    {"label": "Client", "sortable": False}, {"label": "Commande", "sortable": False},
    {"label": "Formation", "sortable": False}, {"label": "Statut", "sortable": True},
    {"label": "Tentatives", "sortable": False}, {"label": "Catégorie d'échec", "sortable": False},
    {"label": "Mis à jour", "sortable": True},
]


@login_required
def provisioning_list(request):
    if not request.user.has_perm("provisioning.view_provisioningrequest"):
        raise PermissionDenied

    status_filter = request.GET.get("status", "")

    requests_qs = ProvisioningRequest.objects.select_related("contact", "order", "course")

    if status_filter in dict(ProvisioningRequest.Status.choices):
        requests_qs = requests_qs.filter(status=status_filter)

    requests_qs = requests_qs.order_by("-updated_at")
    paginator = Paginator(requests_qs, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        "page_obj": page_obj, "headers": _PROVISIONING_HEADERS,
        "status_filter": status_filter,
        "status_options": ProvisioningRequest.Status.choices,
        "can_retry_provisioning": request.user.has_perm("provisioning.retry_provisioning_request"),
        "querystring": _querystring_without_page(request),
    }
    return render(request, "operations/provisioning_list.html", context)


# --- Reconciliation runs (read-only) ---

_RECONCILIATION_HEADERS = [
    {"label": "Démarrée", "sortable": True}, {"label": "Statut", "sortable": True},
    {"label": "Examinées", "sortable": False}, {"label": "Vérifiées R/É", "sortable": False},
    {"label": "En attente/inconnu", "sortable": False}, {"label": "Confirmations", "sortable": False},
    {"label": "Provisionnement", "sortable": False}, {"label": "Réparées", "sortable": False},
    {"label": "Non corrélées", "sortable": False}, {"label": "Erreurs", "sortable": False},
]


@login_required
def reconciliation_list(request):
    if not request.user.has_perm("payments.view_reconciliationrun"):
        raise PermissionDenied

    status_filter = request.GET.get("status", "")

    runs = ReconciliationRun.objects.all()
    if status_filter in dict(ReconciliationRun.RunStatus.choices):
        runs = runs.filter(run_status=status_filter)

    runs = runs.order_by("-started_at")
    paginator = Paginator(runs, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        "page_obj": page_obj, "headers": _RECONCILIATION_HEADERS,
        "status_filter": status_filter,
        "status_options": ReconciliationRun.RunStatus.choices,
        "querystring": _querystring_without_page(request),
    }
    return render(request, "operations/reconciliation_list.html", context)


# --- Administrative audit (read-only) ---

_AUDIT_HEADERS = [
    {"label": "Quand", "sortable": True}, {"label": "Administrateur", "sortable": False},
    {"label": "Action", "sortable": True}, {"label": "Target", "sortable": False},
    {"label": "Précédent → Résultant", "sortable": False}, {"label": "Résultat", "sortable": False},
    {"label": "Motif", "sortable": False},
]


@login_required
def audit_list(request):
    if not request.user.has_perm("payments.view_adminauditlog"):
        raise PermissionDenied

    action_filter = request.GET.get("action_type", "")
    outcome_filter = request.GET.get("outcome", "")
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")

    events = AdminAuditLog.objects.select_related("order", "installment", "payment_attempt")

    if action_filter in dict(AdminAuditLog.ActionType.choices):
        events = events.filter(action_type=action_filter)
    if outcome_filter in dict(AdminAuditLog.OutcomeCategory.choices):
        events = events.filter(outcome_category=outcome_filter)
    parsed_from = parse_date(date_from) if date_from else None
    if parsed_from:
        events = events.filter(created_at__date__gte=parsed_from)
    parsed_to = parse_date(date_to) if date_to else None
    if parsed_to:
        events = events.filter(created_at__date__lte=parsed_to)

    events = events.order_by("-created_at")
    paginator = Paginator(events, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        "page_obj": page_obj, "headers": _AUDIT_HEADERS,
        "action_filter": action_filter, "outcome_filter": outcome_filter,
        "date_from": date_from, "date_to": date_to,
        "action_options": AdminAuditLog.ActionType.choices,
        "outcome_options": AdminAuditLog.OutcomeCategory.choices,
        "querystring": _querystring_without_page(request),
    }
    return render(request, "operations/audit_list.html", context)


# --- Confirmed actions (Phase 8 services, dashboard-native confirmation page) ---

@login_required
def order_cancel(request, reference):
    order = get_object_or_404(Order.objects.select_related("customer"), reference=reference)

    def do_cancel(reason):
        OrderAdministrationService().cancel_order(order.pk, reason, request.user, request)
        return f"Commande {order.reference} annulée."

    context = {
        "title": "Annuler la commande", "description": "Annule cette commande — l'historique de paiement est conservé, aucun remboursement n'est émis.",
        "target_label": f"{order.reference} — {order.customer.email}",
        "back_url": reverse("operations:order_detail", args=[order.reference]),
        "warning": (
            f"Cette commande a des paiements vérifiés (total payé jusqu'ici). L'annulation ne rembourse pas "
            f"l'argent et n'annule pas l'historique de paiement existant." if order.total_paid_amount else ""
        ),
    }
    response = render_confirmation_or_process(
        request, permission="payments.cancel_order", context=context, service_call=do_cancel,
    )
    if response is not None:
        return response
    return redirect("operations:order_detail", reference=order.reference)


@login_required
def order_disposition(request, reference):
    order = get_object_or_404(Order.objects.select_related("customer"), reference=reference)
    disposition = request.GET.get("value") or request.POST.get("value", "")
    if disposition not in dict(Order.ManualDisposition.choices):
        disposition = Order.ManualDisposition.NEEDS_REVIEW

    def do_apply(reason):
        OrderAdministrationService().apply_manual_disposition(order.pk, disposition, reason, request.user, request)
        return f"Disposition définie sur {disposition} pour la commande {order.reference}."

    context = {
        "title": "Appliquer une disposition manuelle", "description": "Ne fait que poser une annotation métier interne — ne modifie jamais le statut de paiement.",
        "target_label": f"{order.reference} — {order.customer.email} → {disposition}",
        "back_url": reverse("operations:order_detail", args=[order.reference]),
    }
    response = render_confirmation_or_process(
        request, permission="payments.apply_manual_disposition", context=context, service_call=do_apply,
    )
    if response is not None:
        return response
    return redirect("operations:order_detail", reference=order.reference)


@login_required
def order_freeze_enrollment(request, reference):
    order = get_object_or_404(Order.objects.select_related("customer"), reference=reference)

    def do_freeze(reason):
        EnrollmentAdministrationService().freeze_order_enrollment(order.pk, reason, request.user, request)
        return f"Inscription suspendue pour la commande {order.reference}."

    context = {
        "title": "Suspendre l'inscription",
        "description": "Suspend l'accès de ce client à la formation ClickFunnels. Il continue de voir la formation mais ne peut plus terminer de leçons tant que ce n'est pas repris. Entièrement réversible.",
        "target_label": f"{order.reference} — {order.customer.email}",
        "back_url": reverse("operations:order_detail", args=[order.reference]),
    }
    response = render_confirmation_or_process(
        request, permission="payments.freeze_enrollment", context=context, service_call=do_freeze,
    )
    if response is not None:
        return response
    return redirect("operations:order_detail", reference=order.reference)


@login_required
def order_resume_enrollment(request, reference):
    order = get_object_or_404(Order.objects.select_related("customer"), reference=reference)

    def do_resume(reason):
        EnrollmentAdministrationService().resume_order_enrollment(order.pk, reason, request.user, request)
        return f"Inscription reprise pour la commande {order.reference}."

    context = {
        "title": "Reprendre l'inscription",
        "description": "Restaure l'accès de ce client à la formation ClickFunnels après une suspension.",
        "target_label": f"{order.reference} — {order.customer.email}",
        "back_url": reverse("operations:order_detail", args=[order.reference]),
    }
    response = render_confirmation_or_process(
        request, permission="payments.resume_enrollment", context=context, service_call=do_resume,
    )
    if response is not None:
        return response
    return redirect("operations:order_detail", reference=order.reference)


@login_required
def installment_cancel(request, pk):
    installment = get_object_or_404(Installment.objects.select_related("order"), pk=pk)

    def do_cancel(reason):
        OrderAdministrationService().cancel_installment(installment.pk, reason, request.user, request)
        return f"Versement n°{installment.sequence} annulé."

    context = {
        "title": "Annuler le versement", "description": "Seuls les versements non payés éligibles peuvent être annulés.",
        "target_label": f"Commande {installment.order.reference} — versement n°{installment.sequence}",
        "back_url": reverse("operations:order_detail", args=[installment.order.reference]),
    }
    response = render_confirmation_or_process(
        request, permission="payments.cancel_installment", context=context, service_call=do_cancel,
    )
    if response is not None:
        return response
    return redirect("operations:order_detail", reference=installment.order.reference)


@login_required
def installment_waive(request, pk):
    installment = get_object_or_404(Installment.objects.select_related("order"), pk=pk)

    def do_waive(reason):
        OrderAdministrationService().waive_installment(installment.pk, reason, request.user, request)
        return f"Versement n°{installment.sequence} exonéré."

    context = {
        "title": "Exonérer le versement", "description": "Une décision métier interne — pas un paiement du fournisseur. Aucun e-mail de confirmation n'est envoyé.",
        "target_label": f"Commande {installment.order.reference} — versement n°{installment.sequence}",
        "back_url": reverse("operations:order_detail", args=[installment.order.reference]),
    }
    response = render_confirmation_or_process(
        request, permission="payments.waive_installment", context=context, service_call=do_waive,
    )
    if response is not None:
        return response
    return redirect("operations:order_detail", reference=installment.order.reference)


@login_required
def payment_attempt_check_status(request, pk):
    attempt = get_object_or_404(PaymentAttempt.objects.select_related("installment__order"), pk=pk)

    def do_check(reason):
        updated = PaymentAttemptAdministrationService().check_tara_status(attempt.pk, reason, request.user, request)
        return f"Statut Tara vérifié pour {updated.tara_product_id} : la tentative est maintenant {updated.status}."

    context = {
        "title": "Vérifier le statut Tara", "description": "Revérifie cette tentative auprès de Tara de manière synchrone — seules les tentatives EN ATTENTE/INCONNU sont éligibles.",
        "target_label": f"{attempt.tara_product_id} (order {attempt.installment.order.reference})",
        "back_url": reverse("operations:order_detail", args=[attempt.installment.order.reference]),
    }
    response = render_confirmation_or_process(
        request, permission="payments.check_tara_status", context=context, service_call=do_check,
    )
    if response is not None:
        return response
    return redirect("operations:order_detail", reference=attempt.installment.order.reference)


@login_required
def confirmation_retry(request, pk):
    confirmation = get_object_or_404(PaymentConfirmation.objects.select_related("order"), pk=pk)

    def do_retry(reason):
        PaymentConfirmationAdministrationService().retry_confirmation(confirmation.pk, reason, request.user, request)
        return f"Confirmation {confirmation.reference} mise en file pour nouvel essai."

    context = {
        "title": "Relancer la confirmation", "description": "Remet cette confirmation dans un état prêt à être traité — c'est le worker qui l'envoie, pas cette page.",
        "target_label": str(confirmation.reference),
        "back_url": reverse("operations:order_detail", args=[confirmation.order.reference]),
    }
    response = render_confirmation_or_process(
        request, permission="payments.retry_payment_confirmation", context=context, service_call=do_retry,
    )
    if response is not None:
        return response
    return redirect("operations:order_detail", reference=confirmation.order.reference)


@login_required
def provisioning_retry(request, pk):
    provisioning_request = get_object_or_404(ProvisioningRequest.objects.select_related("order"), pk=pk)

    def do_retry(reason):
        ProvisioningService().retry_request(provisioning_request.pk, reason, request.user, request)
        return f"Demande de provisionnement n°{provisioning_request.pk} mise en file pour nouvel essai."

    back_url = (
        reverse("operations:order_detail", args=[provisioning_request.order.reference])
        if provisioning_request.order_id else reverse("operations:provisioning_list")
    )
    context = {
        "title": "Relancer le provisionnement", "description": "Remet cette demande d'inscription dans un état prêt à être traitée — aucun appel ClickFunnels n'a lieu sur cette page.",
        "target_label": f"Demande de provisionnement n°{provisioning_request.pk}",
        "back_url": back_url,
    }
    response = render_confirmation_or_process(
        request, permission="provisioning.retry_provisioning_request", context=context, service_call=do_retry,
    )
    if response is not None:
        return response
    return redirect(back_url)
