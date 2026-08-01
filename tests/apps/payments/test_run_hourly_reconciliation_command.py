import threading

import pytest
from django.core.management import call_command, CommandError
from django.db import connection

from apps.payments.models import ReconciliationRun

pytestmark = pytest.mark.django_db


def test_command_runs_cleanly_with_no_active_config():
    call_command("run_hourly_reconciliation")
    run = ReconciliationRun.objects.get()
    assert run.completed_at is not None


@pytest.mark.django_db(transaction=True)
def test_command_reports_skip_without_error_exit():
    """Advisory locks are session-scoped — a genuine "another run in progress" needs a real second connection."""
    holder_ready = threading.Event()
    release_holder = threading.Event()

    def hold_lock():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(782934651)")
        holder_ready.set()
        release_holder.wait(timeout=5)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(782934651)")
        connection.close()

    t = threading.Thread(target=hold_lock)
    t.start()
    try:
        holder_ready.wait(timeout=5)
        call_command("run_hourly_reconciliation")  # should not raise despite being skipped
        run = ReconciliationRun.objects.order_by("-started_at").first()
        assert run.run_status == ReconciliationRun.RunStatus.SKIPPED_OVERLAPPING
    finally:
        release_holder.set()
        t.join(timeout=5)


def test_command_raises_command_error_on_failed_run(mocker):
    mocker.patch(
        "apps.payments.reconciliation_services.ReconciliationService._run_locked",
        side_effect=RuntimeError("boom"),
    )
    with pytest.raises(CommandError):
        call_command("run_hourly_reconciliation")
    run = ReconciliationRun.objects.order_by("-started_at").first()
    assert run.run_status == ReconciliationRun.RunStatus.FAILED
