from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from apps.operations.presentation import reported_unconfirmed_product_ids, sale_state
from apps.payments.models import Installment, Order, PaymentAttempt, PaymentConfirmation, TaraWebhookEvent
from apps.provisioning.models import ProvisioningRequest

# Inbox categories, in priority order. `key` is also the ?type= filter value.
_CATEGORIES = [
    {"key": "verifier", "label": "Paiement à vérifier", "tone": "critical"},
    {"key": "confirmation", "label": "Confirmation en cours", "tone": "warn"},
    {"key": "retard", "label": "Versement en retard", "tone": "critical"},
    {"key": "acces", "label": "Accès non ouvert", "tone": "warn"},
    {"key": "email", "label": "E-mail non envoyé", "tone": "warn"},
    {"key": "associer", "label": "Paiement sans vente", "tone": "accent"},
]
_PER_CATEGORY = 6
_PER_CATEGORY_FILTERED = 50


def _customer_name(contact) -> str:
    """"Sarah N." — falls back to the e-mail when no name is known."""
    if contact is None:
        return "Client inconnu"
    first = (contact.first_name or "").strip()
    last = (contact.last_name or "").strip()
    if first:
        return f"{first} {last[:1]}." if last else first
    return contact.email or "Client inconnu"


def _money(amount, currency="XAF") -> str:
    """1 000 XAF — French thousands separator, no decimals when the amount is whole."""
    if amount is None:
        return f"— {currency}"
    amount = Decimal(amount)
    text = f"{amount:,.0f}" if amount == amount.to_integral_value() else f"{amount:,.2f}"
    return f"{text.replace(',', ' ')} {currency}"


def _age(moment, now) -> str:
    if moment is None:
        return ""
    days = (now - moment).days
    if days <= 0:
        return "aujourd'hui"
    if days == 1:
        return "hier"
    return f"il y a {days} j"


def _shorten(text: str, limit: int = 40) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _payment_items(request, now):
    """Sales where Tara reported a payment we could not confirm, or Tara's answer is still pending."""
    reported = reported_unconfirmed_product_ids()
    candidates = (
        Order.objects.filter(
            Q(installments__payment_attempts__tara_product_id__in=reported)
            | Q(installments__payment_attempts__status__in=[PaymentAttempt.Status.PENDING, PaymentAttempt.Status.UNKNOWN])
        )
        .exclude(status__in=[Order.Status.COMPLETED, Order.Status.CANCELLED])
        .select_related("customer")
        .prefetch_related("installments__payment_attempts")
        .distinct()
        .order_by("created_at")[:200]
    )
    can_verify = request.user.has_perm("payments.check_tara_status")
    items = {"verifier": [], "confirmation": []}
    for order in candidates:
        state = sale_state(order, reported, can_verify=can_verify)
        if state.key not in ("to_verify", "confirming"):
            continue
        key = "verifier" if state.key == "to_verify" else "confirmation"
        items[key].append({
            "title": f"{_customer_name(order.customer)} — {_money(order.installment_amount, order.currency)} — {_shorten(order.course_name)}",
            "detail": state.explanation,
            "age": _age(order.created_at, now),
            "moment": order.created_at,
            "action_label": state.action_label or "Voir la vente",
            "action_url": state.action_url or reverse("operations:order_detail", args=[order.reference]),
            "action_disabled": False,
        })
    return items


def _inbox(request, now):
    today = now.date()
    items = _payment_items(request, now)

    overdue = (
        Installment.objects.exclude(order__status__in=[Order.Status.EXPIRED, Order.Status.CANCELLED])
        .filter(status__in=[Installment.Status.SCHEDULED, Installment.Status.DUE, Installment.Status.PENDING], due_date__lt=today)
        .select_related("order", "order__customer")
        .order_by("due_date")
    )
    items["retard"] = []
    overdue_total = overdue.count()
    for installment in overdue[:_PER_CATEGORY_FILTERED]:
        days_late = (today - installment.due_date).days
        order = installment.order
        items["retard"].append({
            "title": f"{_customer_name(order.customer)} — {_money(installment.expected_amount, installment.currency)} — {_shorten(order.course_name)}",
            "detail": f"Le versement {installment.sequence} sur {order.installment_count} n'est pas payé. Contactez le client.",
            "age": f"en retard de {days_late} j" if days_late > 0 else "dû aujourd'hui",
            "moment": None,
            "action_label": "Voir la vente",
            "action_url": reverse("operations:order_detail", args=[order.reference]),
            "action_disabled": False,
        })

    can_retry_access = request.user.has_perm("provisioning.retry_provisioning_request")
    access = (
        ProvisioningRequest.objects.filter(status__in=[ProvisioningRequest.Status.FAILED, ProvisioningRequest.Status.MANUAL_REVIEW])
        .select_related("contact", "course", "order")
        .order_by("updated_at")
    )
    access_total = access.count()
    items["acces"] = []
    for req in access[:_PER_CATEGORY_FILTERED]:
        if can_retry_access:
            label, url = "Relancer l'accès", reverse("operations:provisioning_retry", args=[req.pk])
        elif req.order_id:
            label, url = "Voir la vente", reverse("operations:order_detail", args=[req.order.reference])
        else:
            label, url = "Voir les accès", reverse("enrollments:list")
        items["acces"].append({
            "title": f"{_customer_name(req.contact)} — {_shorten(req.course.name)}",
            "detail": (
                "Le paiement est confirmé, mais l'accès à la formation n'a pas pu être ouvert dans ClickFunnels."
                if req.order_id else "L'accès à la formation n'a pas pu être ouvert dans ClickFunnels."
            ),
            "age": _age(req.updated_at, now),
            "moment": req.updated_at,
            "action_label": label, "action_url": url, "action_disabled": False,
        })

    can_retry_email = request.user.has_perm("payments.retry_payment_confirmation")
    emails = (
        PaymentConfirmation.objects.filter(status__in=[PaymentConfirmation.Status.FAILED, PaymentConfirmation.Status.MANUAL_REVIEW])
        .select_related("order", "order__customer")
        .order_by("updated_at")
    )
    emails_total = emails.count()
    items["email"] = []
    for confirmation in emails[:_PER_CATEGORY_FILTERED]:
        order = confirmation.order
        if can_retry_email:
            label, url = "Renvoyer l'e-mail", reverse("operations:confirmation_retry", args=[confirmation.pk])
        else:
            label, url = "Voir la vente", reverse("operations:order_detail", args=[order.reference])
        reason = (
            "L'adresse e-mail du client semble invalide — corrigez-la sur sa fiche, puis renvoyez."
            if confirmation.last_failure_category == PaymentConfirmation.FailureCategory.INVALID_RECIPIENT
            else "L'e-mail de confirmation de paiement n'a pas pu être envoyé au client."
        )
        items["email"].append({
            "title": f"{_customer_name(order.customer)} — {_shorten(order.course_name)}",
            "detail": reason,
            "age": _age(confirmation.updated_at, now),
            "moment": confirmation.updated_at,
            "action_label": label, "action_url": url, "action_disabled": False,
        })

    can_attribute = request.user.has_perm("payments.attribute_webhook_payment")
    unmatched = TaraWebhookEvent.objects.filter(
        processing_status=TaraWebhookEvent.ProcessingStatus.UNCORRELATED,
    ).order_by("received_at")
    unmatched_total = unmatched.count()
    items["associer"] = []
    for event in unmatched[:_PER_CATEGORY_FILTERED]:
        items["associer"].append({
            "title": f"Paiement Tara de {_money(event.amount)}" if event.amount is not None else "Paiement Tara (montant inconnu)",
            "detail": "Tara a reçu ce paiement, mais nous ne savons pas à quelle vente il correspond.",
            "age": _age(event.received_at, now),
            "moment": event.received_at,
            "action_label": "Associer à une vente" if can_attribute else "Réservé à l'administrateur",
            "action_url": reverse("operations:webhook_event_attribute", args=[event.pk]),
            "action_disabled": not can_attribute,
        })

    totals = {
        "verifier": len(items["verifier"]), "confirmation": len(items["confirmation"]),
        "retard": overdue_total, "acces": access_total, "email": emails_total, "associer": unmatched_total,
    }
    return items, totals


@login_required
def index(request):
    """
    "À traiter" — the one inbox for non-technical staff (docs/UI_VOCABULARY.md).
    Every item has a plain title, one sentence of explanation and exactly one
    button. Categories are in priority order: a payment Tara reported but we
    could not confirm comes first, because the customer has paid and is waiting.
    `?type=<key>` shows one category only, with more items.
    """
    now = timezone.now()
    items, totals = _inbox(request, now)

    selected = request.GET.get("type", "")
    if selected not in totals:
        selected = ""
    limit = _PER_CATEGORY_FILTERED if selected else _PER_CATEGORY

    sections = []
    for category in _CATEGORIES:
        key = category["key"]
        if selected and key != selected:
            continue
        rows = items[key]
        if not rows:
            continue
        sections.append({
            **category,
            "rows": rows[:limit],
            "total": totals[key],
            "hidden": max(totals[key] - limit, 0),
        })

    chips = [{**c, "count": totals[c["key"]]} for c in _CATEGORIES if totals[c["key"]]]

    week_ago = now - timedelta(days=7)
    received = Installment.objects.filter(status=Installment.Status.PAID, paid_at__gte=week_ago).aggregate(total=Sum("paid_amount"))

    context = {
        "sections": sections,
        "chips": chips,
        "selected": selected,
        "total_items": sum(totals.values()),
        "received_7d": _money(received["total"] or 0),
        "to_verify_count": totals["verifier"] + totals["confirmation"],
        "overdue_count": totals["retard"],
    }
    return render(request, "dashboard/index.html", context)


@login_required
def table_demo(request):
    """Demo view for CRM Data Table components."""
    dummy_data = [
        {"id": 1, "name": "John Doe", "email": "john@example.com", "status": "active"},
        {"id": 2, "name": "Jane Smith", "email": "jane@example.com", "status": "pending"},
        {"id": 3, "name": "Bob Brown", "email": "bob@example.com", "status": "inactive"},
        {"id": 4, "name": "Alice Green", "email": "alice@example.com", "status": "active"},
    ]

    headers = [
        {"label": "Nom", "sortable": True},
        {"label": "Email", "sortable": True},
        {"label": "Statut", "sortable": False},
    ]

    context = {
        "dummy_data": dummy_data,
        "headers": headers,
        "page_obj": {
            "has_other_pages": True,
            "has_previous": False,
            "has_next": True,
            "number": 1,
            "previous_page_number": 0,
            "next_page_number": 2,
            "start_index": 1,
            "end_index": 4,
            "paginator": {"count": 10, "page_range": range(1, 4)},
        },
    }
    return render(request, "dashboard/table_demo.html", context)
