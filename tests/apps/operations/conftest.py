import pytest
from django.contrib.auth.models import Permission, User
from django.test import Client


@pytest.fixture
def ops_client(db):
    """
    Staff user with every 'view_*' permission this operations area's list/
    detail pages check, plus every Phase 8 action permission — mirrors
    superuser_client's blanket access but proves the pages work under
    explicit granted permissions rather than superuser bypass alone.
    """
    user = User.objects.create_user(username="opsstaff", password="x", is_staff=True)
    codenames = [
        "view_order", "view_installment", "view_paymentattempt", "view_tarawebhookevent",
        "view_paymentconfirmation", "view_reconciliationrun", "view_adminauditlog",
        "cancel_order", "apply_manual_disposition", "cancel_installment", "waive_installment",
        "check_tara_status", "retry_payment_confirmation",
    ]
    for codename in codenames:
        perm = Permission.objects.filter(codename=codename).first()
        if perm:
            user.user_permissions.add(perm)
    provisioning_perm = Permission.objects.filter(codename="view_provisioningrequest").first()
    if provisioning_perm:
        user.user_permissions.add(provisioning_perm)
    retry_provisioning_perm = Permission.objects.filter(codename="retry_provisioning_request").first()
    if retry_provisioning_perm:
        user.user_permissions.add(retry_provisioning_perm)

    client = Client()
    client.login(username="opsstaff", password="x")
    return client
