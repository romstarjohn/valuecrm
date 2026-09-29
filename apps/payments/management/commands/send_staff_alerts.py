from django.core.management.base import BaseCommand

from apps.configuration.models import NotificationSettings
from apps.operations.alerts import send_staff_alerts


class Command(BaseCommand):
    help = "Sends the team the new 'À traiter' items (also runs in run_hourly_reconciliation). --dry-run lists them only."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        config = NotificationSettings.current()
        items = send_staff_alerts(dry_run=options["dry_run"])
        for item in items:
            self.stdout.write(f"  {item.category} — {item.title}")
        verb = "seraient envoyés" if options["dry_run"] else "envoyés"
        self.stdout.write(self.style.SUCCESS(f"{len(items)} élément(s) {verb} à {config.alert_email or '(aucune adresse)'}."))
