"""
Team alert e-mail for the "À traiter" inbox (Réglages → Notifications).

Only exceptions a human must handle are sent — never routine sales — and each
item at most once (configuration.StaffAlert). Sent as one digest per hourly
run (ReconciliationService), after the automatic re-checks of that run, so
anything that fixes itself is never e-mailed. A payment Tara reported but we
could not confirm is only alerted once the automatic re-check had at least
ALERT_VERIFY_AFTER to succeed.
"""
from dataclasses import dataclass
from datetime import timedelta
from typing import List

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.configuration.models import NotificationSettings, StaffAlert
from apps.payments.models import Order, PaymentAttempt, PaymentConfirmation, TaraWebhookEvent
from apps.provisioning.models import ProvisioningRequest
from shared.logging_utils import get_logger, log_service_failure, log_service_success

from .presentation import reported_unconfirmed_product_ids, sale_state, short_ref

logger = get_logger(__name__)

ALERT_VERIFY_AFTER = timedelta(hours=1)


@dataclass
class AlertItem:
    key: str
    category: str
    title: str
    explanation: str
    url: str


def _absolute(path: str) -> str:
    return f"{(getattr(settings, 'PUBLIC_BASE_URL', '') or '').rstrip('/')}{path}"


def _name(contact) -> str:
    if contact is None:
        return "Client inconnu"
    full = f"{contact.first_name or ''} {contact.last_name or ''}".strip()
    return full or contact.email or "Client inconnu"


def collect_alert_items(now=None) -> List[AlertItem]:
    now = now or timezone.now()
    items: List[AlertItem] = []

    old_enough = TaraWebhookEvent.objects.filter(
        raw_provider_status__iexact="SUCCESS", received_at__lte=now - ALERT_VERIFY_AFTER,
    ).exclude(processing_status=TaraWebhookEvent.ProcessingStatus.PROCESSED).values_list("tara_product_id", flat=True)
    reported = reported_unconfirmed_product_ids()
    orders = (
        Order.objects.filter(installments__payment_attempts__tara_product_id__in=list(old_enough))
        .exclude(status__in=[Order.Status.CANCELLED])
        .select_related("customer").prefetch_related("installments__payment_attempts").distinct()
    )
    for order in orders:
        state = sale_state(order, reported)
        if state.key != "to_verify":
            continue
        items.append(AlertItem(
            key=f"verify:{order.pk}", category="Paiement à vérifier",
            title=f"Vente {short_ref(order.reference)} — {_name(order.customer)} — {order.installment_amount} {order.currency} — {order.course_name}",
            explanation="Tara signale un paiement que la vérification automatique n'a pas pu confirmer (paiement PayPal, montant différent ou paiement en double possible).",
            url=_absolute(reverse("operations:order_detail", args=[order.reference])),
        ))

    for req in ProvisioningRequest.objects.filter(
        status__in=[ProvisioningRequest.Status.FAILED, ProvisioningRequest.Status.MANUAL_REVIEW],
    ).select_related("contact", "course", "order"):
        items.append(AlertItem(
            key=f"access:{req.pk}", category="Accès non ouvert",
            title=f"{_name(req.contact)} — {req.course.name}",
            explanation="Le paiement est confirmé, mais l'accès à la formation n'a pas pu être ouvert dans ClickFunnels.",
            url=_absolute(
                reverse("operations:order_detail", args=[req.order.reference]) if req.order_id else reverse("dashboard:index")
            ),
        ))

    for confirmation in PaymentConfirmation.objects.filter(
        status=PaymentConfirmation.Status.MANUAL_REVIEW,
    ).select_related("order", "order__customer"):
        items.append(AlertItem(
            key=f"email:{confirmation.pk}", category="E-mail client non envoyé",
            title=f"Vente {short_ref(confirmation.order.reference)} — {_name(confirmation.order.customer)}",
            explanation="L'e-mail « paiement reçu » n'a pas pu être envoyé au client (adresse invalide ou envoi bloqué).",
            url=_absolute(reverse("operations:order_detail", args=[confirmation.order.reference])),
        ))

    for event in TaraWebhookEvent.objects.filter(processing_status=TaraWebhookEvent.ProcessingStatus.UNCORRELATED):
        amount = f"{event.amount} XAF" if event.amount is not None else "montant inconnu"
        items.append(AlertItem(
            key=f"unmatched:{event.pk}", category="Paiement sans vente",
            title=f"Paiement Tara ({amount}) reçu le {timezone.localtime(event.received_at):%d/%m/%Y à %H:%M}",
            explanation="Tara a reçu ce paiement, mais nous ne savons pas à quelle vente il correspond.",
            url=_absolute(reverse("operations:webhook_event_attribute", args=[event.pk])),
        ))
    return items


def send_staff_alerts(now=None, dry_run: bool = False) -> List[AlertItem]:
    """E-mails the NEW items (never alerted before) as one digest. Returns them."""
    config = NotificationSettings.current()
    already = set(StaffAlert.objects.values_list("key", flat=True))
    new_items = [item for item in collect_alert_items(now) if item.key not in already]
    if dry_run or not new_items or not config.alerts_enabled or not config.alert_email:
        return new_items

    count = len(new_items)
    subject = f"{settings.BRAND_NAME} — {count} élément{'s' if count > 1 else ''} à traiter"
    lines = [
        "Bonjour,",
        "",
        f"{count} nouvel{'s' if count > 1 else ''} élément{'s' if count > 1 else ''} demande{'nt' if count > 1 else ''} votre intervention :",
        "",
    ]
    for item in new_items:
        lines += [f"• {item.category} — {item.title}", f"  {item.explanation}", f"  {item.url}", ""]
    lines += [
        f"Tout voir : {_absolute(reverse('dashboard:index'))}",
        "",
        "Vous recevez cet e-mail uniquement quand quelque chose demande une action — jamais pour les ventes normales.",
        f"Modifier l'adresse : {_absolute(reverse('configuration:notifications'))}",
    ]
    try:
        send_mail(subject, "\n".join(lines), settings.DEFAULT_FROM_EMAIL, [config.alert_email], fail_silently=False)
    except Exception as e:  # noqa: BLE001 — nothing recorded, so the next hourly run retries
        log_service_failure(logger, "StaffAlerts", "send_staff_alerts", e)
        return []
    StaffAlert.objects.bulk_create(
        [StaffAlert(key=item.key, summary=f"{item.category} — {item.title}"[:500]) for item in new_items],
        ignore_conflicts=True,
    )
    log_service_success(logger, "StaffAlerts", "send_staff_alerts", created=count)
    return new_items


def send_test_alert() -> None:
    config = NotificationSettings.current()
    send_mail(
        f"{settings.BRAND_NAME} — E-mail de test",
        "Ceci est un e-mail de test : les alertes « À traiter » arriveront à cette adresse.",
        settings.DEFAULT_FROM_EMAIL, [config.alert_email], fail_silently=False,
    )
