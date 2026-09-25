import json

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.payments.models import Order, TaraWebhookEvent
from apps.payments.services import TaraConfigService, TaraVerificationService
from integrations.payments.tara.exceptions import TaraMalformedResponseError
from integrations.payments.tara.schemas import TaraTransactionStatusRequest

# Values printed as-is by --raw-status; every other value is masked (may hold phone numbers or other personal data).
_RAW_STATUS_VISIBLE_KEYS = {
    "status", "message", "productId", "code", "error", "success",
    "amount", "currency", "productPrice", "price", "paymentId", "businessId", "type", "paymentMethod",
}


class Command(BaseCommand):
    help = (
        "Read-only overview of orders -> installments -> payment attempts -> "
        "Tara webhook events, for diagnosing payments that were not credited. "
        "Never changes any state. Prints no customer names, emails or phone "
        "numbers. With --check-tara, also asks Tara for each open or failed "
        "attempt's live status (a read-only provider call — the result is only "
        "printed, never applied; use 'Vérifier le statut Tara' to apply it)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50, help="Most recent N orders (default 50).")
        parser.add_argument("--check-tara", action="store_true", help="Also query Tara's live status per attempt (read-only).")
        parser.add_argument(
            "--raw-status", metavar="ID",
            help="Only show the shape of Tara's raw /transactions/status answer for one id (values masked).",
        )
        parser.add_argument(
            "--paid-list", action="store_true",
            help="Only list Tara's paid transactions (first 100) and mark those matching a received webhook's paymentId.",
        )

    def handle(self, *args, **options):
        out = self.stdout.write
        if options["raw_status"]:
            self._print_raw_status(options["raw_status"])
            return
        if options["paid_list"]:
            self._print_paid_list()
            return
        out(f"PUBLIC_BASE_URL = {settings.PUBLIC_BASE_URL or '(not set)'}")
        out(f"Webhook URL sent to Tara = {settings.PUBLIC_BASE_URL}/api/tara/webhook/")
        out(f"Webhook events received (total) = {TaraWebhookEvent.objects.count()}")

        client = None
        if options["check_tara"]:
            try:
                client = TaraConfigService().get_client()
            except Exception as e:  # noqa: BLE001 — diagnostics must keep going
                out(self.style.WARNING(f"Tara client unavailable ({type(e).__name__}); skipping live checks."))

        orders = Order.objects.order_by("-created_at")[: options["limit"]]
        for order in reversed(list(orders)):
            out(f"\nORDER {order.reference}  status={order.status}  created={order.created_at:%Y-%m-%d %H:%M}")
            for installment in order.installments.order_by("sequence"):
                out(f"  installment #{installment.sequence}  status={installment.status}  paid={installment.paid_amount or 0}")
                attempts = installment.payment_attempts.order_by("created_at")
                if not attempts:
                    out("    (no payment attempt)")
                for attempt in attempts:
                    out(
                        f"    attempt id={attempt.id}  status={attempt.status}  "
                        f"tara_raw={attempt.raw_provider_status or '-'}  product={attempt.tara_product_id}  "
                        f"created={attempt.created_at:%m-%d %H:%M}"
                    )
                    if client is not None and attempt.status != attempt.Status.CREATED:
                        out(f"      TARA LIVE: {self._live_status(client, attempt)}")
                    events = TaraWebhookEvent.objects.filter(tara_product_id=attempt.tara_product_id).order_by("received_at")
                    if not events:
                        out("      (no webhook received)")
                    for event in events:
                        out(
                            f"      webhook {event.received_at:%m-%d %H:%M}  claimed={event.raw_provider_status or '-'}  "
                            f"-> {event.processing_status}/{event.verification_result}  {event.failure_category or ''}  "
                            f"paymentId={event.tara_payment_id or '-'}  amount={event.amount if event.amount is not None else '-'}"
                        )

        uncorrelated = TaraWebhookEvent.objects.filter(payment_attempt__isnull=True).count()
        out(f"\nWebhook events not linked to any attempt = {uncorrelated}")

    @staticmethod
    def _live_status(client, attempt) -> str:
        """Same verification the webhook/admin/reconciliation paths use — printed only, never applied."""
        service = TaraVerificationService()
        try:
            verification = service.verify(client, attempt, service.candidate_payment_ids(attempt))
        except TaraMalformedResponseError as e:
            # TaraClient builds these messages from field names only, never response values.
            return f"lookup failed (TaraMalformedResponseError: {e})"
        except Exception as e:  # noqa: BLE001 — never leak str(e) (may contain a raw provider body)
            return f"lookup failed ({type(e).__name__})"
        if verification.failure_category:
            return f"{verification.normalized.value} — NOT creditable ({verification.failure_category}, amount={verification.amount})"
        return (
            f"{verification.normalized.value} (paymentId={verification.payment_id or '-'}, amount={verification.amount})"
            + ("  => would be credited" if verification.normalized.value == "SUCCESS" else "")
        )

    def _print_raw_status(self, product_id: str) -> None:
        out = self.stdout.write
        client = TaraConfigService().get_client()
        payload = TaraTransactionStatusRequest(
            api_key=client.api_key, business_id=client.business_id, product_id=product_id,
        ).model_dump(by_alias=True)
        data = client._request(
            "POST", f"{client.BASE_URL}/transactions/status", json=payload,
            timeout=(10, 20), allow_redirects=False, operation="payment_diagnostics_raw_status",
        )
        out(f"Top-level JSON type: {type(data).__name__}")
        out(self._describe(data))

    @classmethod
    def _describe(cls, value, indent: int = 0, key: str = "") -> str:
        pad = "  " * indent
        if isinstance(value, dict):
            lines = [f"{pad}{key + ': ' if key else ''}{{"]
            for k, v in value.items():
                lines.append(cls._describe(v, indent + 1, k))
            lines.append(f"{pad}}}")
            return "\n".join(lines)
        if isinstance(value, list):
            lines = [f"{pad}{key + ': ' if key else ''}[ {len(value)} item(s)"]
            if value:
                lines.append(cls._describe(value[0], indent + 1, "first item"))
            lines.append(f"{pad}]")
            return "\n".join(lines)
        if isinstance(value, str) and value.strip()[:1] in ("{", "["):
            try:
                return cls._describe(json.loads(value), indent, f"{key} (JSON text)")
            except ValueError:
                pass
        shown = repr(value) if key in _RAW_STATUS_VISIBLE_KEYS else f"<{type(value).__name__}, masked>"
        return f"{pad}{key}: {shown}"

    def _print_paid_list(self) -> None:
        out = self.stdout.write
        client = TaraConfigService().get_client()
        items = client.list_paid_transactions(start=0, size=100)
        webhook_payment_ids = {
            pid: product for pid, product in
            TaraWebhookEvent.objects.exclude(tara_payment_id="").values_list("tara_payment_id", "tara_product_id")
        }
        out(f"Tara paid transactions returned: {len(items)}")
        for item in items:
            match = webhook_payment_ids.get(item.transaction_id or "")
            marker = f"  <== matches webhook for product {match}" if match else ""
            out(
                f"  transactionId={item.transaction_id or '-'}  amount={item.amount} {item.currency or ''}  "
                f"status={item.status or '-'}  created={item.created_at or '-'}  paid={item.paid_at or '-'}{marker}"
            )
        out(f"Webhook paymentIds on record: {', '.join(webhook_payment_ids) or '(none)'}")

