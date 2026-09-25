"""
Plain-language view of a sale for non-technical staff.

Every staff screen that shows an order (Ventes list/detail, the client page,
the "À traiter" inbox) describes it through `sale_state()` — one sentence of
what is going on and, when there is one, the single next action. Internal
states (PaymentAttempt.Status, webhook processing categories, …) never reach
those screens directly; they stay on the "Technique" pages.

Vocabulary is fixed in docs/UI_VOCABULARY.md — keep both in sync.
"""
from dataclasses import dataclass
from typing import Iterable, Optional, Set

from django.urls import reverse
from django.utils import timezone

from apps.payments.models import Installment, Order, PaymentAttempt, TaraWebhookEvent

# Tones map 1:1 to the existing `.pill.<tone>` classes (static/css/comptoir-components.css).
GOOD, WARN, CRITICAL, NEUTRAL, ACCENT = "good", "warn", "critical", "neutral", "accent"

_OPEN_ATTEMPT_STATUSES = (
    PaymentAttempt.Status.LINK_CREATED, PaymentAttempt.Status.PENDING, PaymentAttempt.Status.UNKNOWN,
    PaymentAttempt.Status.FAILED, PaymentAttempt.Status.EXPIRED,
)


@dataclass
class SaleState:
    key: str
    label: str
    tone: str
    explanation: str
    action_label: str = ""
    action_url: str = ""
    needs_attention: bool = False


def short_ref(reference) -> str:
    """Human-sized order reference (#B40D3341) — the full UUID stays on the detail page."""
    return f"#{str(reference)[:8].upper()}" if reference else ""


def reported_unconfirmed_product_ids(product_ids: Optional[Iterable[str]] = None) -> Set[str]:
    """
    productIds for which Tara sent a SUCCESS notification that we have not
    (yet) been able to confirm — the "Tara says paid, we show unpaid" case.
    One query for a whole page of orders.
    """
    qs = TaraWebhookEvent.objects.filter(raw_provider_status__iexact="SUCCESS").exclude(
        processing_status=TaraWebhookEvent.ProcessingStatus.PROCESSED,
    )
    if product_ids is not None:
        qs = qs.filter(tara_product_id__in=list(product_ids))
    return set(qs.values_list("tara_product_id", flat=True))


def attempt_to_verify(order: Order, reported_product_ids: Set[str] = frozenset()) -> Optional[PaymentAttempt]:
    """The payment attempt a "Vérifier le paiement" button should check, if any."""
    attempts = [
        attempt
        for installment in order.installments.all()
        if installment.status not in (Installment.Status.PAID, Installment.Status.WAIVED, Installment.Status.CANCELLED)
        for attempt in installment.payment_attempts.all()
        if attempt.status in _OPEN_ATTEMPT_STATUSES
    ]
    reported = [a for a in attempts if a.tara_product_id in reported_product_ids]
    if reported:
        return max(reported, key=lambda a: a.created_at)
    with_link = [a for a in attempts if a.general_link or a.status in (PaymentAttempt.Status.PENDING, PaymentAttempt.Status.UNKNOWN)]
    return max(with_link, key=lambda a: a.created_at) if with_link else None


def sale_state(order: Order, reported_product_ids: Optional[Set[str]] = None, can_verify: bool = True) -> SaleState:
    """
    `order` should have `installments__payment_attempts` prefetched for lists.
    `reported_product_ids`: pass the result of reported_unconfirmed_product_ids()
    for a whole page to avoid one query per order.
    """
    installments = list(order.installments.all())
    attempts = [a for i in installments for a in i.payment_attempts.all()]
    if reported_product_ids is None:
        reported_product_ids = reported_unconfirmed_product_ids(a.tara_product_id for a in attempts)

    paid = [i for i in installments if i.status == Installment.Status.PAID]
    total = len(installments)

    def verify_action(state: SaleState) -> SaleState:
        attempt = attempt_to_verify(order, reported_product_ids)
        if attempt and can_verify:
            state.action_label = "Vérifier le paiement"
            state.action_url = reverse("operations:payment_attempt_check_status", args=[attempt.pk])
        return state

    if order.status == Order.Status.COMPLETED:
        return SaleState("paid", "Payée", GOOD, "Tout est réglé.")
    if order.status == Order.Status.CANCELLED:
        return SaleState("cancelled", "Annulée", NEUTRAL, "Cette vente a été annulée. Rien à faire.")
    if order.status == Order.Status.EXPIRED:
        return SaleState(
            "expired", "Expirée", NEUTRAL,
            "Le client n'a pas payé dans le délai. Elle se réactive toute seule si un paiement arrive.",
        )
    if order.status == Order.Status.SUSPENDED:
        return SaleState("suspended", "Suspendue", WARN, "Vente suspendue manuellement.", needs_attention=True)

    reported = [a for a in attempts if a.tara_product_id in reported_product_ids and a.status != PaymentAttempt.Status.SUCCEEDED]
    if reported:
        return verify_action(SaleState(
            "to_verify", "À vérifier", CRITICAL,
            "Tara signale un paiement que nous n'avons pas encore pu confirmer. Vérifiez-le auprès de Tara.",
            needs_attention=True,
        ))
    if any(a.status in (PaymentAttempt.Status.PENDING, PaymentAttempt.Status.UNKNOWN) for a in attempts):
        return verify_action(SaleState(
            "confirming", "Confirmation en cours", WARN,
            "Un paiement est en cours de confirmation par Tara.",
            needs_attention=True,
        ))

    if order.status in (Order.Status.ACTIVE, Order.Status.PAST_DUE):
        # Nothing moves installments to DUE/PAST_DUE automatically — lateness is
        # decided by the due date itself, same rule as the "À traiter" inbox.
        today = timezone.localdate()
        late = order.status == Order.Status.PAST_DUE or any(
            i.status not in (Installment.Status.PAID, Installment.Status.WAIVED, Installment.Status.CANCELLED)
            and i.due_date < today
            for i in installments
        )
        progress = f"{len(paid)} versement{'s' if len(paid) > 1 else ''} payé{'s' if len(paid) > 1 else ''} sur {total}."
        if late:
            return SaleState("late", "Versement en retard", CRITICAL, f"{progress} Un versement est en retard.", needs_attention=True)
        return SaleState("in_progress", "Paiement en plusieurs fois", ACCENT, progress)

    # PENDING — nothing paid yet.
    if any(a.general_link for a in attempts):
        return SaleState(
            "awaiting_payment", "En attente de paiement", NEUTRAL,
            "Le lien de paiement a été ouvert, le client n'a pas encore payé.",
        )
    return SaleState(
        "not_started", "Paiement non démarré", NEUTRAL,
        "Le paiement n'a pas pu démarrer chez Tara. La vente expirera automatiquement.",
    )
