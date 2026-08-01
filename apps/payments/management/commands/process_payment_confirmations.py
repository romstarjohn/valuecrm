from django.core.management.base import BaseCommand

from shared.logging_utils import get_logger, log_service_start, log_service_success
from apps.payments.services import PaymentConfirmationDeliveryService

logger = get_logger(__name__)


class Command(BaseCommand):
    help = (
        "Processes durable PaymentConfirmation work records (Phase 7, "
        "docs/TARA_INTEGRATION_PROJECT.md) that are PENDING or due for a "
        "retry (FAILED with next_attempt_at elapsed), sending each one's "
        "email outside of any payment transaction. Safe to invoke "
        "repeatedly/concurrently — each confirmation is claimed with a "
        "row lock before it's sent. Intended to run frequently via an "
        "external/hosting cron until a general queue exists; the same "
        "PaymentConfirmationDeliveryService.process_pending() will be reused "
        "by the future hourly scheduler (Phase 9)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        log_service_start(logger, "ProcessPaymentConfirmations", "handle")
        processed = PaymentConfirmationDeliveryService().process_pending(limit=options["limit"])
        log_service_success(logger, "ProcessPaymentConfirmations", "handle")
        self.stdout.write(self.style.SUCCESS(f"Processed {processed} pending payment confirmation(s)."))
