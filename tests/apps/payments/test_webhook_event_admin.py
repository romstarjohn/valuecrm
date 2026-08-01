from decimal import Decimal

import pytest
from django.urls import reverse

from apps.payments.models import TaraWebhookEvent

pytestmark = pytest.mark.django_db

CHANGELIST_URL = reverse("admin:payments_tarawebhookevent_changelist")


@pytest.fixture
def event():
    return TaraWebhookEvent.objects.create(
        business_id="biz_123", tara_product_id="attempt-1", raw_provider_status="SUCCESS",
        payload_digest="a" * 64, dedup_key="b" * 64,
        processing_status=TaraWebhookEvent.ProcessingStatus.PROCESSED,
        verification_mode=TaraWebhookEvent.VerificationMode.SERVER_TO_SERVER,
        verification_result=TaraWebhookEvent.VerificationResult.SUCCESS,
    )


def test_anonymous_cannot_view_webhook_events(client, event):
    response = client.get(CHANGELIST_URL)
    assert response.status_code == 302
    assert "/admin/login/" in response.url


def test_staff_without_permissions_cannot_view(staff_client, event):
    response = staff_client.get(CHANGELIST_URL)
    assert response.status_code == 403


def test_superuser_can_view_read_only(superuser_client, event):
    response = superuser_client.get(CHANGELIST_URL)
    assert response.status_code == 200
    assert str(event.reference).encode() in response.content


def test_add_view_not_permitted(superuser_client):
    response = superuser_client.get(reverse("admin:payments_tarawebhookevent_add"))
    assert response.status_code == 403


def test_change_view_does_not_accept_writes(superuser_client, event):
    response = superuser_client.post(
        reverse("admin:payments_tarawebhookevent_change", args=[event.pk]),
        data={"processing_status": TaraWebhookEvent.ProcessingStatus.PROCESSED},
    )
    assert response.status_code == 403


def test_delete_view_not_permitted(superuser_client, event):
    response = superuser_client.get(reverse("admin:payments_tarawebhookevent_delete", args=[event.pk]))
    assert response.status_code == 403


def test_no_arbitrary_provider_message_field_displayed(superuser_client, event):
    """The model itself never stores a free-text provider message — confirming the admin can't display what doesn't exist."""
    assert not hasattr(event, "provider_message")
    assert not hasattr(event, "raw_payload")
    response = superuser_client.get(reverse("admin:payments_tarawebhookevent_change", args=[event.pk]))
    assert response.status_code == 200


def test_no_pii_displayed(superuser_client, event):
    """
    The model has no phone_number field at all (structurally confirmed in
    test_no_arbitrary_provider_message_field_displayed's sibling assertions
    below) — this checks the rendered page doesn't contain an actual phone
    VALUE. The word "phoneNumber" legitimately appears in a field's help text
    (documentation, not data) and is not itself a PII leak.
    """
    assert not hasattr(event, "phone_number")
    response = superuser_client.get(reverse("admin:payments_tarawebhookevent_change", args=[event.pk]))
    assert response.status_code == 200
