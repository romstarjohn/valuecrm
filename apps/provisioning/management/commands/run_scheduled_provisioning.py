from django.core.management.base import BaseCommand

from shared.logging_utils import get_logger, log_service_start, log_service_success
from apps.provisioning.models import ProvisioningRequest
from apps.provisioning.services import ProvisioningService

logger = get_logger(__name__)


class Command(BaseCommand):
    help = (
        "Executes all SCHEDULED provisioning requests (Product.provisioning_policy "
        "== SCHEDULED). Intended to run nightly via an external/hosting cron, "
        "independently of Tara payment reconciliation — keeping these as two "
        "separate commands is itself part of separating the payment lifecycle "
        "from the provisioning lifecycle. Does not retry FAILED requests; that's "
        "an explicit operator action (Admin 'Retry'), not automatic."
    )

    def handle(self, *args, **options):
        log_service_start(logger, "RunScheduledProvisioning", "handle")
        service = ProvisioningService()
        requests = ProvisioningRequest.objects.filter(status=ProvisioningRequest.Status.SCHEDULED)

        processed = 0
        for req in requests:
            service.execute(req)
            processed += 1

        log_service_success(logger, "RunScheduledProvisioning", "handle", processed=processed)
        self.stdout.write(self.style.SUCCESS(f"Processed {processed} scheduled provisioning request(s)."))
