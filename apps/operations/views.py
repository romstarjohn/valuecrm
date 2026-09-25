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
    {"label": "Client", "sortable": False},
    {"label": "Offre", "sortable": False},
    {"label": "Montant", "sortable": False},
    {"label": "État", "sortable": False},
    {"label": "Date", "sortable": False},
    {"label": "Réf.", "sortable": False},
]

# Plain-language "État" filter of the Ventes list (docs/UI_VOCABULARY.md) ->
# Order.status values. "a_verifier" is computed separately (_needs_attention_q)
# because it mirrors apps/operations/presentation.py::sale_state's
# needs_attention rule, which isn't a single status.
_SALE_FILTERS = [
    ("a_verifier", "À vérifier"),
    ("en_attente", "En attente de paiement"),
    ("en_cours", "Paiement en plusieurs fois"),
    ("payees", "Payées"),
    ("expirees", "Expirées"),
    ("annulees", "Annulées"),
]
_SALE_FILTER_STATUSES = {
    "en_attente": [Order.Status.PENDING],
    "en_cours": [Order.Status.ACTIVE, Order.Status.PAST_DUE],
    "payees": [Order.Status.COMPLETED],
    "expirees": [Order.Status.EXPIRED],
    "annulees": [Order.Status.CANCELLED],
}
_OPEN_ORDER_STATUSES = [Order.Status.PENDING, Order.Status.ACTIVE, Order.Status.PAST_DUE, Order.Status.SUSPENDED]


def _needs_attention_q():
    """
    Same rule as sale_state(...).needs_attention, as one ORM filter: an open
    sale where Tara reported a payment we couldn't confirm, a payment still
    being confirmed, a late installment, or a manual suspension.
    """
    from .presentation import reported_unconfirmed_product_ids

    reported_order_ids = (
        PaymentAttempt.objects.filter(tara_product_id__in=reported_unconfirmed_product_ids())
        .exclude(status=PaymentAttempt.Status.SUCCEEDED)
        .values("installment__order_id")
    )
    confirming_order_ids = PaymentAttempt.objects.filter(
        status__in=[PaymentAttempt.Status.PENDING, PaymentAttempt.Status.UNKNOWN],
    ).values("installment__order_id")
    return (
        Q(status__in=_OPEN_ORDER_STATUSES, id__in=reported_order_ids)
        | Q(status__in=_OPEN_ORDER_STATUSES, id__in=confirming_order_ids)
        | Q(status=Order.Status.PAST_DUE)
        | Q(status=Order.Status.ACTIVE, installments__status__in=[Installment.Status.DUE, Installment.Status.FAILED])
        | Q(status=Order.Status.SUSPENDED)
    )


@login_required
def hub(request):
    """
    "Outils techniques" — landing page of the Technique menu: one card per
    technical list saying what it contains and when to use it, with a count.
    Everyday work happens in "À traiter" / "Ventes" (docs/UI_VOCABULARY.md).
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

    unresolved_notifications = TaraWebhookEvent.objects.exclude(
        processing_status=TaraWebhookEvent.ProcessingStatus.PROCESSED,
    ).count()

    # One card per technical list: what it is, when to use it, one count.
    tools = [
        {
            "title": "Paiements Tara", "icon": "fa-solid fa-money-check-dollar",
            "url": reverse("operations:payment_attempt_list"),
            "what": "Chaque demande de paiement envoyée à Tara (une par lien de paiement) et sa réponse brute.",
            "when": "pour savoir si Tara a bien créé un lien, ou revérifier un paiement précis.",
            "count": payments_awaiting_verification, "count_label": "en attente", "alert": bool(payments_awaiting_verification),
        },
        {
            "title": "Notifications Tara", "icon": "fa-solid fa-bell",
            "url": reverse("operations:webhook_event_list"),
            "what": "Les messages envoyés par Tara quand un client paie, et ce que nous en avons fait.",
            "when": "quand un client dit avoir payé mais que la vente ne bouge pas.",
            "count": unresolved_notifications, "count_label": "non traitées", "alert": bool(unresolved_notifications),
        },
        {
            "title": "Échéancier", "icon": "fa-solid fa-calendar-days",
            "url": reverse("operations:installment_list"),
            "what": "Tous les versements prévus, dus ou payés, toutes ventes confondues.",
            "when": "pour voir ce qui doit être encaissé dans les prochains jours.",
            "count": due_installments, "count_label": "dus", "alert": False,
        },
        {
            "title": "E-mails de confirmation", "icon": "fa-solid fa-envelope",
            "url": reverse("operations:confirmation_list"),
            "what": "Les e-mails « paiement reçu » envoyés aux clients, et les échecs d'envoi.",
            "when": "quand un client dit ne pas avoir reçu sa confirmation.",
            "count": confirmations_awaiting_delivery, "count_label": "à envoyer", "alert": False,
        },
        {
            "title": "Accès ClickFunnels (tâches)", "icon": "fa-solid fa-key",
            "url": reverse("operations:provisioning_list"),
            "what": "Les tâches automatiques qui ouvrent l'accès à la formation après un paiement.",
            "when": "quand un client a payé mais n'a pas accès à sa formation.",
            "count": enrollments_awaiting_processing, "count_label": "en attente", "alert": False,
        },
        {
            "title": "Ventes à examiner", "icon": "fa-solid fa-triangle-exclamation",
            "url": reverse("operations:order_list") + "?needs_review=1",
            "what": "Ventes avec un paiement incertain, un e-mail ou un accès bloqué.",
            "when": "pour une revue complète des cas bloqués.",
            "count": manual_review_items, "count_label": "à examiner", "alert": bool(manual_review_items),
        },
        {
            "title": "Rapprochement automatique", "icon": "fa-solid fa-rotate",
            "url": reverse("operations:reconciliation_list"),
            "what": "Le contrôle horaire qui revérifie les paiements incertains et relance e-mails et accès.",
            "when": "pour vérifier que les tâches automatiques tournent bien.",
            "count": failed_reconciliation_runs, "count_label": "en échec", "alert": bool(failed_reconciliation_runs),
        },
        {
            "title": "Journal d'audit", "icon": "fa-solid fa-clock-rotate-left",
            "url": reverse("operations:audit_list"),
            "what": "Qui a fait quelle action manuelle (annulation, vérification, accès…), quand et pourquoi.",
            "when": "pour retrouver l'origine d'un changement.",
            "count": None, "count_label": "", "alert": False,
        },
    ]

    context = {
        "tools": tools,
        "active_plans": active_plans,
        "pending_orders": pending_orders,
        "payments_awaiting_verification": payments_awaiting_verification,
        "due_installments": due_installments,
        "confirmations_awaiting_delivery": confirmations_awaiting_delivery,
        "enrollments_awaiting_processing": enrollments_awaiting_processing,
        "manual_review_items": manual_review_items,
        "failed_reconciliation_runs": failed_reconciliation_runs,
        "unresolved_notifications": unresolved_notifications,
        "recent_verified_payments": recent_verified_payments,
    }
    return render(request, "operations/hub.html", context)


_ORDER_ELIGIBLE_Q = (
    Q(access_policy=PaymentPlan.AccessPolicy.FIRST_INSTALLMENT, _paid_count__gte=1)
    | Q(access_policy=PaymentPlan.AccessPolicy.FULL_PAYMENT, _installment_count__gt=0, _unsettled_count=0)
)


@login_required
def order_list(request):
    """
    "Ventes" — every sale with a plain-language state (sale_state) and its
    next step. Main filters are the ones a non-technical user needs (état,
    offre, période, recherche); the older technical filters (status, plan,
    access, disposition, needs_review) keep working as query parameters and
    sit under "Plus de filtres".
    """
    if not request.user.has_perm("payments.view_order"):
        raise PermissionDenied

    from apps.courses.models import Course

    from .presentation import reported_unconfirmed_product_ids, sale_state

    query = request.GET.get("q", "").strip()
    etat_filter = request.GET.get("etat", "")
    if etat_filter not in dict(_SALE_FILTERS):
        etat_filter = ""
    status_filter = request.GET.get("status", "")
    plan_filter = request.GET.get("plan", "")
    course_filter = request.GET.get("course", "")
    access_filter = request.GET.get("access", "")
    disposition_filter = request.GET.get("disposition", "")
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    needs_review = request.GET.get("needs_review", "")

    orders = Order.objects.select_related("customer", "plan", "course").prefetch_related(
        "installments__payment_attempts", "payment_confirmations", "provisioning_requests",
    )

    if query:
        reference_query = query.lstrip("#")  # "#B40D3341" as shown in the list
        orders = orders.filter(
            Q(reference__icontains=reference_query)
            | Q(customer__first_name__icontains=query)
            | Q(customer__last_name__icontains=query)
            | Q(customer__email__icontains=query)
            | Q(plan_name__icontains=query)
            | Q(course_name__icontains=query)
        )

    if etat_filter == "a_verifier":
        orders = orders.filter(_needs_attention_q()).distinct()
    elif etat_filter:
        orders = orders.filter(status__in=_SALE_FILTER_STATUSES[etat_filter])
    elif status_filter in dict(Order.Status.choices):
        orders = orders.filter(status=status_filter)
    elif not query:
        # Expired checkouts are noise in the default view; filtering by status
        # EXPIRED or searching (e.g. by customer email) still finds them.
        orders = orders.exclude(status=Order.Status.EXPIRED)

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

    page_orders = list(page_obj)
    reported = reported_unconfirmed_product_ids(
        a.tara_product_id for o in page_orders for i in o.installments.all() for a in i.payment_attempts.all()
    )
    can_verify = request.user.has_perm("payments.check_tara_status")
    for order in page_orders:
        _attach_order_display_fields(order)
        order.state = sale_state(order, reported, can_verify=can_verify)

    has_more_filters = bool(status_filter or plan_filter or access_filter or disposition_filter or needs_review == "1")

    context = {
        "page_obj": page_obj,
        "headers": _ORDER_HEADERS,
        "query": query,
        "etat_filter": etat_filter,
        "etat_options": _SALE_FILTERS,
        "to_verify_count": Order.objects.filter(_needs_attention_q()).distinct().count(),
        "course_options": Course.objects.filter(orders__isnull=False).distinct().order_by("name").values_list("id", "name"),
        "has_more_filters": has_more_filters,
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


_CONFIRMATION_IN_WORDS = {
    PaymentConfirmation.Status.SENT: ("E-mail envoyé", "good"),
    PaymentConfirmation.Status.PENDING: ("En cours d'envoi", "neutral"),
    PaymentConfirmation.Status.SENDING: ("En cours d'envoi", "neutral"),
    PaymentConfirmation.Status.FAILED: ("Échec de l'envoi", "critical"),
    PaymentConfirmation.Status.MANUAL_REVIEW: ("Envoi bloqué — à relancer", "critical"),
}


def _sale_target(order: Order) -> str:
    """'Vente #B40D3341 — Sarah N., 1 000 XAF' — how an order is named on confirmation pages."""
    from apps.contacts.templatetags.vente_format import format_money

    from .presentation import short_ref

    customer = order.customer
    name = " ".join(part for part in [customer.first_name, f"{customer.last_name[:1]}." if customer.last_name else ""] if part)
    return f"Vente {short_ref(order.reference)} — {name or customer.email}, {format_money(order.total_expected_amount, order.currency)}"


def _installment_in_words(installment: Installment, total: int) -> dict:
    """'Versement 1 sur 3 — 1 000 XAF — Payé le 16/08' for the Vente page."""
    from apps.contacts.templatetags.vente_format import format_money

    title = f"Versement {installment.sequence} sur {total}" if total > 1 else "Paiement"
    amount = format_money(installment.expected_amount, installment.currency)
    status = installment.status
    if status == Installment.Status.PAID:
        detail = f"Payé le {installment.paid_at:%d/%m/%Y}" if installment.paid_at else "Payé"
        if installment.paid_amount is not None and installment.paid_amount != installment.expected_amount:
            detail += f" ({format_money(installment.paid_amount, installment.currency)} reçus)"
        tone = "good"
    elif status == Installment.Status.WAIVED:
        detail, tone = "Le client en a été dispensé", "neutral"
    elif status == Installment.Status.CANCELLED:
        detail, tone = "Annulé", "neutral"
    elif status == Installment.Status.FAILED:
        detail, tone = f"Paiement échoué — dû le {installment.due_date:%d/%m/%Y}", "critical"
    elif installment.due_date and installment.due_date < date.today():
        detail, tone = f"En retard — dû le {installment.due_date:%d/%m/%Y}", "critical"
    else:
        detail, tone = (f"Dû le {installment.due_date:%d/%m/%Y}" if installment.due_date else "À payer"), "neutral"
    return {
        "title": title, "amount": amount, "detail": detail, "tone": tone,
        "is_open": status not in (Installment.Status.PAID, Installment.Status.WAIVED, Installment.Status.CANCELLED),
    }


def _access_in_words(order: Order, provisioning_requests) -> dict:
    """Course access for the Vente page — never ClickFunnels job states."""
    if any(getattr(r, "cf_suspended", False) for r in provisioning_requests) or order.status == Order.Status.SUSPENDED:
        return {"label": "Accès suspendu", "tone": "warn", "explanation": "Le client voit la formation mais ne peut plus avancer tant que l'accès n'est pas rétabli."}
    if any(r.status == ProvisioningRequest.Status.COMPLETED for r in provisioning_requests):
        return {"label": "Accès ouvert", "tone": "good", "explanation": "Le client peut suivre la formation dans ClickFunnels."}
    failed = [r for r in provisioning_requests if r.status in (ProvisioningRequest.Status.FAILED, ProvisioningRequest.Status.MANUAL_REVIEW)]
    if failed:
        return {
            "label": "Échec de l'ouverture de l'accès", "tone": "critical",
            "explanation": "Le paiement est confirmé mais l'accès n'a pas pu être ouvert dans ClickFunnels. Relancez l'ouverture.",
            "retry_request": failed[0],
        }
    if provisioning_requests:
        return {"label": "Ouverture en cours", "tone": "neutral", "explanation": "L'accès va être ouvert automatiquement dans les prochaines minutes."}
    if order.display_access_eligible:
        return {"label": "Ouverture en cours", "tone": "neutral", "explanation": "L'accès va être ouvert automatiquement."}
    if order.status in (Order.Status.CANCELLED, Order.Status.EXPIRED):
        return {"label": "Pas d'accès", "tone": "neutral", "explanation": "Cette vente n'a pas abouti."}
    if order.access_policy == PaymentPlan.AccessPolicy.FULL_PAYMENT:
        return {"label": "En attente du paiement", "tone": "neutral", "explanation": "L'accès s'ouvre automatiquement une fois tout payé."}
    return {"label": "En attente du paiement", "tone": "neutral", "explanation": "L'accès s'ouvre automatiquement dès le premier paiement."}


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

    from .presentation import sale_state, short_ref

    can_check_tara_status = request.user.has_perm("payments.check_tara_status")
    state = sale_state(order, can_verify=can_check_tara_status)
    for installment in installments:
        installment.plain = _installment_in_words(installment, len(installments))
    for confirmation in confirmations:
        confirmation.plain_label, confirmation.plain_tone = _CONFIRMATION_IN_WORDS.get(
            confirmation.status, (confirmation.status, "neutral"),
        )
    access = _access_in_words(order, list(provisioning_requests))

    context = {
        "order": order,
        "state": state,
        "short_ref": short_ref(order.reference),
        "access": access,
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
    {"label": "Vente", "sortable": False}, {"label": "Client", "sortable": False},
    {"label": "N°", "sortable": False}, {"label": "Dû le", "sortable": True},
    {"label": "Montant dû", "sortable": False}, {"label": "Payé", "sortable": False},
    {"label": "Statut", "sortable": True}, {"label": "À vérifier", "sortable": False},
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
    {"label": "Statut interne", "sortable": True}, {"label": "Réponse de Tara", "sortable": False},
    {"label": "Réf. produit Tara (productId)", "sortable": False}, {"label": "Réf. paiement Tara (paymentId)", "sortable": False},
    {"label": "Montant", "sortable": False}, {"label": "Vente / versement", "sortable": False},
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
    {"label": "Réf. notification", "sortable": False}, {"label": "Montant", "sortable": False},
    {"label": "Vente", "sortable": False},
    {"label": "Vérification", "sortable": False}, {"label": "Traitement", "sortable": True},
    {"label": "Motif d'échec", "sortable": False}, {"label": "Reçue le", "sortable": True},
]


@login_required
def webhook_event_list(request):
    if not request.user.has_perm("payments.view_tarawebhookevent"):
        raise PermissionDenied

    processing_status_filter = request.GET.get("processing_status", "")
    verification_result_filter = request.GET.get("verification_result", "")
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")

    events = TaraWebhookEvent.objects.select_related("payment_attempt__installment__order__customer")

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
    {"label": "Vente / client", "sortable": False}, {"label": "Versement", "sortable": False},
    {"label": "Canal", "sortable": False}, {"label": "Statut", "sortable": True},
    {"label": "Essais", "sortable": False}, {"label": "Prochain essai", "sortable": False},
    {"label": "Envoyé le", "sortable": True},
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
    {"label": "Client", "sortable": False}, {"label": "Vente", "sortable": False},
    {"label": "Formation", "sortable": False}, {"label": "Statut", "sortable": True},
    {"label": "Essais", "sortable": False}, {"label": "Motif d'échec", "sortable": False},
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
    {"label": "Accès ClickFunnels", "sortable": False}, {"label": "Réparées", "sortable": False},
    {"label": "Non corrélées", "sortable": False}, {"label": "Ventes expirées", "sortable": False},
    {"label": "Erreurs", "sortable": False},
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
    {"label": "Action", "sortable": True}, {"label": "Cible", "sortable": False},
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
        return "Vente annulée. Les versements restants ne seront plus demandés."

    context = {
        "title": "Annuler la vente",
        "description": (
            "La vente passe à « Annulée » et les versements restants ne sont plus demandés au client. "
            "Les paiements déjà reçus restent enregistrés."
        ),
        "target_label": _sale_target(order),
        "back_url": reverse("operations:order_detail", args=[order.reference]),
        "warning": (
            "Le client a déjà payé une partie de cette vente. Annuler ne le rembourse pas : "
            "un éventuel remboursement se fait à part, directement dans Tara." if order.total_paid_amount else ""
        ),
        "submit_label": "Annuler la vente",
        "reason_placeholder": "Ex. : le client a renoncé, doublon de commande…",
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

    from shared.templatetags.status_labels import status_label

    def do_apply(reason):
        OrderAdministrationService().apply_manual_disposition(order.pk, disposition, reason, request.user, request)
        return f"Note « {status_label(disposition)} » ajoutée à la vente."

    context = {
        "title": "Signaler la vente",
        "description": (
            f"Ajoute la note interne « {status_label(disposition)} » à cette vente. "
            "C'est un simple repère pour l'équipe : le paiement et l'accès du client ne changent pas."
        ),
        "target_label": _sale_target(order),
        "back_url": reverse("operations:order_detail", args=[order.reference]),
        "submit_label": "Ajouter la note",
        "reason_placeholder": "Ex. : le client dit avoir payé deux fois, à vérifier avec Tara…",
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
        return "Accès suspendu. Vous pourrez le rétablir à tout moment depuis cette vente."

    context = {
        "title": "Suspendre l'accès à la formation",
        "description": (
            "Le client voit toujours la formation mais ne peut plus avancer dans les leçons. "
            "Rien n'est supprimé : vous pouvez rétablir l'accès à tout moment."
        ),
        "target_label": _sale_target(order),
        "back_url": reverse("operations:order_detail", args=[order.reference]),
        "submit_label": "Suspendre l'accès",
        "reason_placeholder": "Ex. : versement en retard depuis 15 jours…",
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
        return "Accès rétabli. Le client peut de nouveau suivre la formation."

    context = {
        "title": "Rétablir l'accès à la formation",
        "description": "Le client retrouve un accès complet à la formation, là où il s'était arrêté.",
        "target_label": _sale_target(order),
        "back_url": reverse("operations:order_detail", args=[order.reference]),
        "submit_label": "Rétablir l'accès",
        "reason_placeholder": "Ex. : le versement en retard a été payé…",
    }
    response = render_confirmation_or_process(
        request, permission="payments.resume_enrollment", context=context, service_call=do_resume,
    )
    if response is not None:
        return response
    return redirect("operations:order_detail", reference=order.reference)


@login_required
def installment_cancel(request, pk):
    installment = get_object_or_404(Installment.objects.select_related("order", "order__customer"), pk=pk)

    def do_cancel(reason):
        OrderAdministrationService().cancel_installment(installment.pk, reason, request.user, request)
        return f"Versement {installment.sequence} annulé : il ne sera plus demandé au client."

    context = {
        "title": "Annuler ce versement",
        "description": (
            "Ce versement ne sera plus demandé au client. Utilisez-le pour une erreur ou un versement qui n'a plus lieu d'être. "
            "Un versement déjà payé ne peut pas être annulé."
        ),
        "target_label": f"{_sale_target(installment.order)} — versement {installment.sequence} sur {installment.order.installment_count}",
        "back_url": reverse("operations:order_detail", args=[installment.order.reference]),
        "submit_label": "Annuler ce versement",
        "reason_placeholder": "Ex. : versement créé par erreur…",
    }
    response = render_confirmation_or_process(
        request, permission="payments.cancel_installment", context=context, service_call=do_cancel,
    )
    if response is not None:
        return response
    return redirect("operations:order_detail", reference=installment.order.reference)


@login_required
def installment_waive(request, pk):
    installment = get_object_or_404(Installment.objects.select_related("order", "order__customer"), pk=pk)

    def do_waive(reason):
        OrderAdministrationService().waive_installment(installment.pk, reason, request.user, request)
        return f"Le client est dispensé du versement {installment.sequence}."

    context = {
        "title": "Dispenser le client de ce versement",
        "description": (
            "Le client n'aura pas à payer ce versement : il est considéré comme réglé, sans argent reçu (geste commercial, arrangement…). "
            "Si c'était le dernier versement, l'accès à la formation s'ouvre. Aucun e-mail n'est envoyé."
        ),
        "target_label": f"{_sale_target(installment.order)} — versement {installment.sequence} sur {installment.order.installment_count}",
        "back_url": reverse("operations:order_detail", args=[installment.order.reference]),
        "submit_label": "Dispenser le client",
        "reason_placeholder": "Ex. : geste commercial accordé par la direction…",
    }
    response = render_confirmation_or_process(
        request, permission="payments.waive_installment", context=context, service_call=do_waive,
    )
    if response is not None:
        return response
    return redirect("operations:order_detail", reference=installment.order.reference)


@login_required
def payment_attempt_check_status(request, pk):
    attempt = get_object_or_404(
        PaymentAttempt.objects.select_related("installment__order", "installment__order__customer"), pk=pk,
    )
    order = attempt.installment.order

    def do_check(reason):
        updated = PaymentAttemptAdministrationService().check_tara_status(attempt.pk, reason, request.user, request)
        if updated.status == PaymentAttempt.Status.SUCCEEDED:
            order.refresh_from_db()
            if order.status == Order.Status.COMPLETED:
                return "Paiement confirmé par Tara — la vente est payée. Le client va recevoir son e-mail et son accès."
            return "Paiement confirmé par Tara — le versement est enregistré comme payé."
        if updated.status == PaymentAttempt.Status.FAILED:
            return "Tara indique que ce paiement a échoué. Aucun argent n'a été reçu pour ce versement."
        return "Tara ne confirme pas encore ce paiement. Réessayez plus tard ; si le client a bien payé, contactez le support Tara."

    context = {
        "title": "Vérifier le paiement auprès de Tara",
        "description": (
            "Nous demandons à Tara si ce paiement a bien été reçu. Si Tara le confirme, la vente passe à « Payée » "
            "et le client reçoit son e-mail et son accès. Sinon, rien ne change."
        ),
        "target_label": _sale_target(order),
        "back_url": reverse("operations:order_detail", args=[order.reference]),
        "submit_label": "Vérifier maintenant",
        "reason_placeholder": "Ex. : le client dit avoir payé le 16/08 par Orange Money…",
    }
    response = render_confirmation_or_process(
        request, permission="payments.check_tara_status", context=context, service_call=do_check,
    )
    if response is not None:
        return response
    return redirect("operations:order_detail", reference=attempt.installment.order.reference)


@login_required
def confirmation_retry(request, pk):
    confirmation = get_object_or_404(PaymentConfirmation.objects.select_related("order", "order__customer"), pk=pk)

    def do_retry(reason):
        PaymentConfirmationAdministrationService().retry_confirmation(confirmation.pk, reason, request.user, request)
        return "L'e-mail de confirmation va être renvoyé dans les prochaines minutes."

    context = {
        "title": "Renvoyer l'e-mail de confirmation",
        "description": (
            f"L'e-mail de confirmation de paiement sera renvoyé à {confirmation.order.customer.email} "
            "dans les prochaines minutes (envoi automatique, pas depuis cette page)."
        ),
        "target_label": _sale_target(confirmation.order),
        "back_url": reverse("operations:order_detail", args=[confirmation.order.reference]),
        "submit_label": "Renvoyer l'e-mail",
        "reason_placeholder": "Ex. : le client n'a rien reçu, adresse corrigée…",
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
        return "L'ouverture de l'accès va être relancée dans les prochaines minutes."

    back_url = (
        reverse("operations:order_detail", args=[provisioning_request.order.reference])
        if provisioning_request.order_id else reverse("operations:provisioning_list")
    )
    context = {
        "title": "Relancer l'ouverture de l'accès",
        "description": (
            "Nous réessayons d'ouvrir l'accès du client à la formation dans ClickFunnels, "
            "automatiquement dans les prochaines minutes."
        ),
        "target_label": (
            _sale_target(provisioning_request.order) if provisioning_request.order_id
            else f"Accès à « {provisioning_request.course.name} » — {provisioning_request.contact.email}"
        ),
        "back_url": back_url,
        "submit_label": "Relancer l'ouverture",
        "reason_placeholder": "Ex. : problème ClickFunnels corrigé…",
    }
    response = render_confirmation_or_process(
        request, permission="provisioning.retry_provisioning_request", context=context, service_call=do_retry,
    )
    if response is not None:
        return response
    return redirect(back_url)
