from django.core.management.base import BaseCommand, CommandError

from shared.logging_utils import get_logger, log_service_start, log_service_success
from apps.payments.models import ReconciliationRun
from apps.payments.reconciliation_services import ReconciliationService

logger = get_logger(__name__)


class Command(BaseCommand):
    help = (
        "Hourly reconciliation (Phase 9, docs/TARA_INTEGRATION_PROJECT.md): verifies "
        "stale PENDING/UNKNOWN PaymentAttempts via check_transaction_status(), pulls "
        "the documented paid-transaction list for reporting only, drives the existing "
        "confirmation/provisioning workers, and repairs any missing durable follow-up "
        "work. Concurrency-safe — a Postgres advisory lock ensures overlapping "
        "invocations never process the same run; a crashed run releases the lock "
        "automatically and never permanently blocks later runs. "
        "Intended invocation: hourly via external cron/systemd/platform scheduler, e.g. "
        "`0 * * * * cd /path/to/app && python manage.py run_hourly_reconciliation` "
        "(see docs/OPERATIONS.md for the full runbook entry)."
    )

    def handle(self, *args, **options):
        log_service_start(logger, "RunHourlyReconciliation", "handle")
        run = ReconciliationService().run()
        log_service_success(logger, "RunHourlyReconciliation", "handle", run_status=run.run_status)

        summary = (
            f"status={run.run_status} attempts_examined={run.attempts_examined} "
            f"status_checks={run.status_checks} verified_successes={run.verified_successes} "
            f"verified_failures={run.verified_failures} still_pending_or_unknown={run.still_pending_or_unknown} "
            f"confirmations(processed/sent/failed)={run.confirmation_jobs_processed}/{run.confirmation_jobs_sent}/{run.confirmation_jobs_failed} "
            f"provisioning(processed/completed/failed)={run.provisioning_jobs_processed}/{run.provisioning_jobs_completed}/{run.provisioning_jobs_failed} "
            f"missing_work_repaired={run.missing_work_repaired} "
            f"uncorrelated_transaction_list_records={run.uncorrelated_transaction_list_records} "
            f"safe_error_count={run.safe_error_count}"
        )

        if run.run_status == ReconciliationRun.RunStatus.FAILED:
            self.stderr.write(self.style.ERROR(summary))
            raise CommandError("Hourly reconciliation run failed.")
        elif run.run_status == ReconciliationRun.RunStatus.SKIPPED_OVERLAPPING:
            self.stdout.write(self.style.WARNING(f"Skipped — another run is already in progress. {summary}"))
        elif run.run_status == ReconciliationRun.RunStatus.PARTIAL_FAILURE:
            self.stdout.write(self.style.WARNING(f"Completed with errors. {summary}"))
        else:
            self.stdout.write(self.style.SUCCESS(summary))
