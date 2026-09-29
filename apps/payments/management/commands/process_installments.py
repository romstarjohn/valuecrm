from django.conf import settings
from django.core.management.base import BaseCommand

from apps.payments.services import InstallmentCollectionService


class Command(BaseCommand):
    help = (
        "Installments after the first: reminders, overdue marking, suspension of access "
        "for non-payment and automatic restoration once paid (see InstallmentCollectionService). "
        "Also runs automatically in run_hourly_reconciliation. Use --dry-run to only list what would happen."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="List planned reminders/suspensions/restorations without doing anything.")

    def handle(self, *args, **options):
        self.stdout.write(
            f"Règles : rappel {settings.INSTALLMENT_REMINDER_DAYS_BEFORE} j avant et le jour J (si e-mail), "
            f"suspension {settings.INSTALLMENT_SUSPENSION_GRACE_HOURS} h après la fin du jour d'échéance "
            f"({settings.BUSINESS_TIME_ZONE})."
        )
        result = InstallmentCollectionService().run(dry_run=options["dry_run"])
        if options["dry_run"]:
            for line in result["planned"]:
                self.stdout.write(f"  {line}")
            self.stdout.write(self.style.WARNING(f"Dry run : {len(result['planned'])} action(s) prévue(s), rien n'a été fait."))
            return
        self.stdout.write(self.style.SUCCESS(
            f"{result['reminders_sent']} rappel(s) envoyé(s), {result['accesses_suspended']} accès suspendu(s), "
            f"{result['accesses_resumed']} accès rétabli(s), {result['marked_late']} vente(s) passée(s) en retard."
        ))
