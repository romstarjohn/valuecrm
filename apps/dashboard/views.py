from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.db.models.functions import TruncDate
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from apps.payments.models import Installment, Order, TaraWebhookEvent
from apps.provisioning.models import ProvisioningRequest


@login_required
def index(request):
    """
    Vue d'ensemble — the day-to-day landing page (docs/PRODUCT_CADRAGE_PMI.md
    O4/O5, O2). Every KPI links to the filtered list it summarizes; the
    action queue below merges the three exception types a gestionnaire/
    administrateur actually has to resolve (échéance en retard, échec
    d'inscription, paiement non rapproché) into one worklist, sorted oldest
    first within each type. Never mixes "accès offert" into a payments total —
    that distinction isn't derivable from the current schema (ProvisioningRequest
    requires an Order), so it's deliberately left out rather than guessed at.
    """
    now = timezone.now()
    today = now.date()
    week_ago = now - timedelta(days=7)
    week_ahead = today + timedelta(days=7)

    open_installment_statuses = [Installment.Status.SCHEDULED, Installment.Status.DUE, Installment.Status.PENDING]

    received_qs = Installment.objects.filter(status=Installment.Status.PAID, paid_at__gte=week_ago)
    received = received_qs.aggregate(total=Sum("paid_amount"))
    payments_received_7d_count = received_qs.count()
    payments_received_7d_amount = received["total"] or 0

    # Sparkline on the "Paiements reçus" tile: real daily totals for the last
    # 7 days, not a decorative curve — a flat 0-day still plots as 0, never
    # interpolated or invented.
    daily_totals = {
        row["day"]: float(row["total"] or 0)
        for row in received_qs.annotate(day=TruncDate("paid_at")).values("day").annotate(total=Sum("paid_amount"))
    }
    days = [today - timedelta(days=n) for n in range(6, -1, -1)]
    series = [daily_totals.get(d, 0.0) for d in days]
    peak = max(series) or 1.0
    sparkline_points = " ".join(
        f"{i * (100 / 6):.1f},{24 - (value / peak) * 22:.1f}" for i, value in enumerate(series)
    )

    # Installments of EXPIRED orders stay technically payable (a late payment
    # still credits) but are not collectable work — kept out of the KPIs.
    open_installments = Installment.objects.exclude(order__status=Order.Status.EXPIRED)
    overdue_qs = open_installments.filter(status__in=open_installment_statuses, due_date__lt=today)
    overdue_sum = overdue_qs.aggregate(total=Sum("expected_amount"))
    overdue_count = overdue_qs.count()
    overdue_amount = overdue_sum["total"] or 0

    upcoming_count = open_installments.filter(
        status__in=open_installment_statuses, due_date__gte=today, due_date__lte=week_ahead,
    ).count()

    enrollment_failures_qs = ProvisioningRequest.objects.filter(
        status__in=[ProvisioningRequest.Status.FAILED, ProvisioningRequest.Status.MANUAL_REVIEW],
    ).select_related("contact", "course", "order")
    enrollment_failures_count = enrollment_failures_qs.count()

    unreconciled_qs = TaraWebhookEvent.objects.filter(processing_status=TaraWebhookEvent.ProcessingStatus.UNCORRELATED)
    unreconciled_sum = unreconciled_qs.aggregate(total=Sum("amount"))
    unreconciled_count = unreconciled_qs.count()
    unreconciled_amount = unreconciled_sum["total"] or 0

    # --- Unified action queue: oldest-first within each type, capped so the
    # page stays a worklist, not a second copy of every list screen. ---
    queue = []
    for installment in overdue_qs.select_related("order", "order__customer").order_by("due_date")[:4]:
        days_late = (today - installment.due_date).days
        queue.append({
            "kind": "retard", "kind_label": "Retard", "css": "critical",
            "title": f"{installment.order.customer.first_name} {installment.order.customer.last_name} — {installment.order.course_name}",
            "detail": f"Versement n°{installment.sequence}/{installment.order.installment_count} impayé · {installment.expected_amount} {installment.currency} dus",
            "age": f"en retard {days_late} j" if days_late > 0 else "échue aujourd'hui",
            "sort_key": (0, days_late * -1),
            "action_label": "Voir la commande",
            "action_url": reverse("operations:order_detail", args=[installment.order.reference]),
            "action_disabled": False,
        })

    for req in enrollment_failures_qs.order_by("updated_at")[:4]:
        queue.append({
            "kind": "echec", "kind_label": "Échec inscription", "css": "warn",
            "title": f"{req.contact.first_name} {req.contact.last_name} — {req.course.name}",
            "detail": "Paiement confirmé, accès ClickFunnels non ouvert" if req.order_id else "Inscription non ouverte",
            "age": req.updated_at.strftime("depuis le %d/%m"),
            "sort_key": (1, 0),
            "action_label": "Voir l'inscription",
            "action_url": reverse("operations:provisioning_list") + f"?status={req.status}",
            "action_disabled": False,
        })

    for event in unreconciled_qs.order_by("received_at")[:4]:
        queue.append({
            "kind": "rapprochement", "kind_label": "Rapprochement", "css": "accent",
            "title": f"Paiement direct Tara — réf. {event.reference}",
            "detail": (
                f"{event.amount} XAF reçus, aucun identifiant de commande correspondant" if event.amount
                else "Montant non communiqué par Tara, aucun identifiant de commande correspondant"
            ),
            "age": event.received_at.strftime("reçu le %d/%m"),
            "sort_key": (2, 0),
            "action_label": "Associer" if request.user.has_perm("payments.attribute_webhook_payment") else "Réservé à l'administrateur",
            "action_disabled": not request.user.has_perm("payments.attribute_webhook_payment"),
            "action_url": reverse("operations:webhook_event_attribute", args=[event.pk]),
        })

    queue.sort(key=lambda row: row["sort_key"])

    context = {
        "payments_received_7d_count": payments_received_7d_count,
        "payments_received_7d_amount": payments_received_7d_amount,
        "overdue_count": overdue_count,
        "overdue_amount": overdue_amount,
        "upcoming_count": upcoming_count,
        "enrollment_failures_count": enrollment_failures_count,
        "unreconciled_count": unreconciled_count,
        "unreconciled_amount": unreconciled_amount,
        "sparkline_points": sparkline_points,
        "queue": queue,
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
