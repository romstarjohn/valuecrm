from django.core.management.base import BaseCommand

from shared.logging_utils import get_logger, log_service_start, log_service_success
from apps.provisioning.services import ProvisioningService

logger = get_logger(__name__)


class Command(BaseCommand):
    help = (
        "Processes durable ProvisioningRequest work records (Order/course "
        "flow, docs/TARA_INTEGRATION_PROJECT.md) that are PENDING or FAILED, "
        "calling ClickFunnels outside of any payment transaction. Never "
        "picks up MANUAL_REVIEW requests — those require an explicit "
        "operator action (Admin 'Retry'). Safe to invoke repeatedly/"
        "concurrently: ProvisioningService._claim() atomically claims each "
        "request with a row lock before processing it. This is the worker "
        "for requests created inline by checkout/webhook processing. "
        "The same ProvisioningService.process_pending() will be reused by "
        "the future hourly scheduler (Phase 9)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        log_service_start(logger, "ProcessPendingProvisioning", "handle")
        processed = ProvisioningService().process_pending(limit=options["limit"])
        log_service_success(logger, "ProcessPendingProvisioning", "handle")
        self.stdout.write(self.style.SUCCESS(f"Processed {processed} pending provisioning request(s)."))
