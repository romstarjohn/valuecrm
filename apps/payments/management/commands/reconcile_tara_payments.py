from django.core.management.base import BaseCommand
from django.utils import timezone

from shared.constants import PAYMENT_NEEDS_REVIEW_RETRY_WINDOW_DAYS
from shared.logging_utils import get_logger, log_service_start, log_service_success, log_service_failure
from apps.payments.models import Payment, ReconciliationRun
from apps.payments.services import PaymentMatchingService
from apps.provisioning.services import ProvisioningService

logger = get_logger(__name__)


class Command(BaseCommand):
    help = (
        "Re-runs local matching for stale NEEDS_REVIEW legacy Payments (e.g. after a "
        "ProductMapping/Contact fix). Makes no Tara network call. "
        "Phase 9 note: this command previously also pulled Tara's transaction list to "
        "auto-match unsolicited payments by phone/email/amount. TaraClient."
        "list_paid_transactions() now implements the real documented POST "
        "/tara/paid/transactionlist contract (docs/Tara_API_Reference_Technique.docx §7), "
        "whose response has no productId/phone/email at all — incompatible with that "
        "approach, and Phase 9 explicitly forbids granting payment credit by matching "
        "phone, email, amount, or course. That responsibility moved to "
        "run_hourly_reconciliation, which verifies PaymentAttempts by exact productId "
        "via check_transaction_status() and uses the transaction list for reporting only."
    )

    def handle(self, *args, **options):
        log_service_start(logger, "ReconcileTaraPayments", "handle")
        run = ReconciliationRun.objects.create(started_at=timezone.now())

        matching_service = PaymentMatchingService()
        provisioning_service = ProvisioningService()
        errors = 0

        cutoff = timezone.now() - timezone.timedelta(days=PAYMENT_NEEDS_REVIEW_RETRY_WINDOW_DAYS)
        stale_needs_review = Payment.objects.filter(status=Payment.Status.NEEDS_REVIEW, created_at__gte=cutoff)
        for payment in stale_needs_review:
            try:
                matching_service.match_and_process(payment)
                if payment.status == Payment.Status.MATCHED:
                    provisioning_service.create_request_from_payment(payment)
            except Exception as e:
                errors += 1
                log_service_failure(logger, "ReconcileTaraPayments", "handle", e, payment_id=payment.id)

        run.completed_at = timezone.now()
        run.errors = errors
        run.save()

        log_service_success(logger, "ReconcileTaraPayments", "handle", errors=errors)
        self.stdout.write(self.style.SUCCESS(f"Stale NEEDS_REVIEW recheck complete: {errors} error(s)."))
