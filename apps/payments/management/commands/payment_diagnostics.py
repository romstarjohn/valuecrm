from django.conf import settings
from django.core.management.base import BaseCommand

from apps.payments.models import Order, TaraWebhookEvent
from apps.payments.services import TaraConfigService
from integrations.payments.tara.exceptions import TaraMalformedResponseError
from integrations.payments.tara.schemas import TaraTransactionStatusRequest

# Values printed as-is by --raw-status; every other value is masked (may hold phone numbers or other personal data).
_RAW_STATUS_VISIBLE_KEYS = {"status", "message", "productId", "code", "error", "success"}


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
            "--raw-status", metavar="PRODUCT_ID",
            help="Only show the shape of Tara's raw /transactions/status answer for one productId (values masked).",
        )

    def handle(self, *args, **options):
        out = self.stdout.write
        if options["raw_status"]:
            self._print_raw_status(options["raw_status"])
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
                        out(f"      TARA LIVE: {self._live_status(client, attempt.tara_product_id)}")
                    events = TaraWebhookEvent.objects.filter(tara_product_id=attempt.tara_product_id).order_by("received_at")
                    if not events:
                        out("      (no webhook received)")
                    for event in events:
                        out(
                            f"      webhook {event.received_at:%m-%d %H:%M}  claimed={event.raw_provider_status or '-'}  "
                            f"-> {event.processing_status}/{event.verification_result}  {event.failure_category or ''}"
                        )

        uncorrelated = TaraWebhookEvent.objects.filter(payment_attempt__isnull=True).count()
        out(f"\nWebhook events not linked to any attempt = {uncorrelated}")

    @staticmethod
    def _live_status(client, product_id: str) -> str:
        try:
            response = client.check_transaction_status(product_id)
        except TaraMalformedResponseError as e:
            # TaraClient builds these messages from field names only, never response values.
            return f"lookup failed (TaraMalformedResponseError: {e})"
        except Exception as e:  # noqa: BLE001 — never leak str(e) (may contain a raw provider body)
            return f"lookup failed ({type(e).__name__})"
        return f"{response.status} (normalized {response.normalized_status.value})"

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
        shown = repr(value) if key in _RAW_STATUS_VISIBLE_KEYS else f"<{type(value).__name__}, masked>"
        return f"{pad}{key}: {shown}"

