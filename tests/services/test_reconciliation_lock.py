"""Release-gate verification: Postgres advisory lock lifecycle for ReconciliationService.run()."""
import threading
from unittest.mock import Mock

import pytest
from django.db import connection

from apps.payments.models import ReconciliationRun, TaraConfig
from apps.payments.reconciliation_services import ReconciliationService
from shared.security import encrypt_value

pytestmark = pytest.mark.django_db

_LOCK_KEY = 782934651


def _lock_free_via_other_connection() -> bool:
    """True if a genuinely different DB session can acquire the lock right now."""
    result = {}

    def attempt():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [_LOCK_KEY])
            result["acquired"] = cursor.fetchone()[0]
        if result["acquired"]:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [_LOCK_KEY])
        connection.close()

    t = threading.Thread(target=attempt)
    t.start()
    t.join(timeout=5)
    return result["acquired"]


@pytest.fixture
def mocked_client(mocker):
    mock_client = Mock()
    mock_client.list_paid_transactions.return_value = []
    mocker.patch("apps.payments.reconciliation_services.TaraConfigService.get_client", return_value=mock_client)
    return mock_client


@pytest.mark.django_db(transaction=True)
def test_successful_run_releases_lock(mocked_client):
    run = ReconciliationService().run()
    assert run.run_status in (ReconciliationRun.RunStatus.SUCCESS, ReconciliationRun.RunStatus.PARTIAL_FAILURE)
    assert _lock_free_via_other_connection() is True


@pytest.mark.django_db(transaction=True)
def test_failed_run_releases_lock(mocker):
    mocker.patch(
        "apps.payments.reconciliation_services.ReconciliationService._run_locked",
        side_effect=RuntimeError("boom"),
    )
    run = ReconciliationService().run()
    assert run.run_status == ReconciliationRun.RunStatus.FAILED
    assert _lock_free_via_other_connection() is True


@pytest.mark.django_db(transaction=True)
def test_second_run_same_connection_can_reacquire(mocked_client):
    first = ReconciliationService().run()
    second = ReconciliationService().run()
    assert first.run_status != ReconciliationRun.RunStatus.SKIPPED_OVERLAPPING
    assert second.run_status != ReconciliationRun.RunStatus.SKIPPED_OVERLAPPING


@pytest.mark.django_db(transaction=True)
def test_genuinely_concurrent_connection_cannot_acquire_while_held(mocked_client):
    holder_ready = threading.Event()
    release_holder = threading.Event()

    def hold_lock():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [_LOCK_KEY])
        holder_ready.set()
        release_holder.wait(timeout=5)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [_LOCK_KEY])
        connection.close()

    t = threading.Thread(target=hold_lock)
    t.start()
    try:
        holder_ready.wait(timeout=5)
        run = ReconciliationService().run()
        assert run.run_status == ReconciliationRun.RunStatus.SKIPPED_OVERLAPPING
    finally:
        release_holder.set()
        t.join(timeout=5)
