from django.conf import settings
from django.core.management.base import BaseCommand

from apps.payments.services import OrderExpiryService


class Command(BaseCommand):
    help = (
        "Marks unpaid checkout orders EXPIRED (see OrderExpiryService). Also "
        "runs automatically as the last step of run_hourly_reconciliation. "
        "Use --dry-run to only list what would be expired."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="List eligible orders without changing anything.")

    def handle(self, *args, **options):
        service = OrderExpiryService()
        self.stdout.write(
            f"Rules: no payment link after {settings.ORDER_FAILED_CHECKOUT_EXPIRY_MINUTES} min, "
            f"otherwise unpaid after {settings.ORDER_PENDING_EXPIRY_DAYS} days."
        )
        if options["dry_run"]:
            orders = list(service.eligible_orders().order_by("created_at"))
            for order in orders:
                reason = "no payment link" if not order._has_link else "unpaid, past expiry window"
                self.stdout.write(f"  would expire {order.reference}  created={order.created_at:%Y-%m-%d %H:%M}  ({reason})")
            self.stdout.write(self.style.WARNING(f"Dry run: {len(orders)} order(s) would be expired."))
            return
        expired = service.expire_stale_orders()
        self.stdout.write(self.style.SUCCESS(f"Expired {expired} order(s)."))
