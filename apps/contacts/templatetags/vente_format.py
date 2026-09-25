"""
Plain-language formatting for sales shown to non-technical staff
(docs/UI_VOCABULARY.md): amounts as "1 000 XAF", price terms in words.
Used by the Ventes pages (apps/operations) and the client pages.
"""
from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()

_NBSP = " "


def format_money(amount, currency="XAF") -> str:
    try:
        value = Decimal(str(amount if amount not in (None, "") else 0))
    except InvalidOperation:
        return f"{amount} {currency}".strip()
    if value == value.to_integral_value():
        text = f"{int(value):,}".replace(",", _NBSP)
    else:
        text = f"{value:,.2f}".replace(",", _NBSP).replace(".", ",")
    return f"{text}{_NBSP}{currency}" if currency else text


def format_formule(order) -> str:
    """'1 paiement de 1 000 XAF' / '3 × 20 000 XAF, tous les 30 jours' — from the order's frozen terms."""
    amount = format_money(order.installment_amount, order.currency)
    if order.installment_count <= 1:
        return f"1 paiement de {amount}"
    return f"{order.installment_count} × {amount}, tous les {order.installment_interval_days} jours"


@register.filter
def money(amount, currency="XAF"):
    return format_money(amount, currency)


@register.filter
def formule(order):
    return format_formule(order)


# AdminAuditLog.ActionType / OutcomeCategory labels are English in the model;
# staff pages show these instead (docs/UI_VOCABULARY.md).
ACTION_LABELS = {
    "CHECK_TARA_STATUS": "Paiement vérifié auprès de Tara",
    "RETRY_CONFIRMATION": "E-mail de confirmation renvoyé",
    "RETRY_PROVISIONING": "Ouverture de l'accès relancée",
    "CANCEL_ORDER": "Vente annulée",
    "CANCEL_INSTALLMENT": "Versement annulé",
    "WAIVE_INSTALLMENT": "Client dispensé d'un versement",
    "APPLY_MANUAL_DISPOSITION": "Vente signalée",
    "FREEZE_ENROLLMENT": "Accès suspendu",
    "RESUME_ENROLLMENT": "Accès rétabli",
    "ATTRIBUTE_PAYMENT": "Paiement Tara associé à une vente",
}
OUTCOME_LABELS = {
    "SUCCESS": "Fait",
    "NO_OP_ALREADY_IN_STATE": "Déjà fait",
    "REJECTED_INVALID_STATE": "Refusé",
    "REJECTED_PERMISSION": "Non autorisé",
    "PROVIDER_NON_FINAL": "Pas encore confirmé",
    "FAILED": "Échec",
}


@register.filter
def action_label(action_type):
    return ACTION_LABELS.get(action_type, action_type)


@register.filter
def outcome_label(outcome):
    return OUTCOME_LABELS.get(outcome, outcome)
