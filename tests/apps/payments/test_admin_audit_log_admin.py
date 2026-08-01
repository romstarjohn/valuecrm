import pytest
from django.urls import reverse

from apps.payments.admin_services import AdminAuditService
from apps.payments.models import AdminAuditLog

pytestmark = pytest.mark.django_db

CHANGELIST_URL = reverse("admin:payments_adminauditlog_changelist")


@pytest.fixture
def log_entry(db):
    return AdminAuditService().record(
        administrator=None, action_type=AdminAuditLog.ActionType.CANCEL_ORDER,
        target_type=AdminAuditLog.TargetType.ORDER, target_reference="ORD-TEST",
        reason="test reason", outcome_category=AdminAuditLog.OutcomeCategory.SUCCESS,
    )


def test_anonymous_cannot_view(client, log_entry):
    resp = client.get(CHANGELIST_URL)
    assert resp.status_code == 302
    assert "/admin/login/" in resp.url


def test_staff_without_permissions_cannot_view(staff_client, log_entry):
    resp = staff_client.get(CHANGELIST_URL)
    assert resp.status_code == 403


def test_superuser_can_view_read_only(superuser_client, log_entry):
    resp = superuser_client.get(CHANGELIST_URL)
    assert resp.status_code == 200
    assert str(log_entry.reference).encode() in resp.content


def test_add_view_not_permitted(superuser_client):
    resp = superuser_client.get(reverse("admin:payments_adminauditlog_add"))
    assert resp.status_code == 403


def test_change_view_does_not_accept_writes(superuser_client, log_entry):
    resp = superuser_client.post(
        reverse("admin:payments_adminauditlog_change", args=[log_entry.pk]),
        data={"reason": "hacked reason"},
    )
    assert resp.status_code == 403
    log_entry.refresh_from_db()
    assert log_entry.reason == "test reason"


def test_delete_view_not_permitted(superuser_client, log_entry):
    resp = superuser_client.get(reverse("admin:payments_adminauditlog_delete", args=[log_entry.pk]))
    assert resp.status_code == 403
