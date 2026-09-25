from decimal import Decimal

import pytest
from django.contrib.admin.models import LogEntry
from django.urls import reverse

from apps.courses.models import Course
from apps.payments.models import PaymentPlan

ADD_URL = reverse("admin:payments_paymentplan_add")
CHANGELIST_URL = reverse("admin:payments_paymentplan_changelist")


@pytest.fixture
def course(db):
    return Course.objects.create(cf_course_id="crs_1", name="Bootcamp", workspace_id="ws_1")


@pytest.fixture
def other_course(db):
    return Course.objects.create(cf_course_id="crs_2", name="Advanced Bootcamp", workspace_id="ws_1")


def valid_payload(course, **overrides):
    payload = {
        "code": "bootcamp-3x",
        "name": "Bootcamp — 3 installments",
        "description": "",
        "course": course.pk,
        "currency": PaymentPlan.Currency.XAF,
        "installment_count": 3,
        "installment_amount": "34000.00",
        "installment_interval_days": 30,
        "access_policy": PaymentPlan.AccessPolicy.FULL_PAYMENT,
        "is_active": "on",
        "display_order": 0,
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
def test_authorized_admin_can_create_plan(superuser_client, course):
    response = superuser_client.post(ADD_URL, data=valid_payload(course), follow=True)
    assert response.status_code == 200
    plan = PaymentPlan.objects.get(code="bootcamp-3x")
    assert plan.course_id == course.id
    assert plan.computed_total == Decimal("102000.00")


@pytest.mark.django_db
def test_creation_is_audited_via_django_admin_log_entry(superuser_client, course):
    """Reuses django.contrib.admin.models.LogEntry — the only generic audit mechanism in this repo (see Phase 0)."""
    superuser_client.post(ADD_URL, data=valid_payload(course), follow=True)
    plan = PaymentPlan.objects.get(code="bootcamp-3x")
    entry = LogEntry.objects.get(object_id=str(plan.pk))
    assert entry.action_flag == 1  # ADDITION


@pytest.mark.django_db
def test_anonymous_user_cannot_create_plan(client, course):
    response = client.post(ADD_URL, data=valid_payload(course))
    assert response.status_code == 302
    assert "/admin/login/" in response.url
    assert not PaymentPlan.objects.filter(code="bootcamp-3x").exists()


@pytest.mark.django_db
def test_staff_without_permissions_cannot_create_plan(staff_client, course):
    """staff_client (tests/conftest.py) is is_staff=True with no model permissions — matches the repo's existing fixture, not a superuser. Blocked at the admin-site level now (SuperuserOnlyAdminSite, AGENT.md), so a redirect rather than a per-model 403."""
    response = staff_client.post(ADD_URL, data=valid_payload(course))
    assert response.status_code == 302
    assert not PaymentPlan.objects.filter(code="bootcamp-3x").exists()


@pytest.mark.django_db
def test_missing_course_is_rejected(superuser_client, course):
    payload = valid_payload(course)
    del payload["course"]
    response = superuser_client.post(ADD_URL, data=payload)
    assert response.status_code == 200  # re-rendered form with errors, not a redirect
    assert not PaymentPlan.objects.exists()


@pytest.mark.django_db
def test_nonexistent_course_id_is_rejected(superuser_client, course):
    """A raw course pk that doesn't exist must be rejected server-side — never trusted from the browser."""
    bogus_course = Course(pk=999999)
    response = superuser_client.post(ADD_URL, data=valid_payload(bogus_course))
    assert response.status_code == 200
    assert not PaymentPlan.objects.exists()


@pytest.mark.django_db
def test_duplicate_code_is_rejected_via_admin(superuser_client, course, other_course):
    superuser_client.post(ADD_URL, data=valid_payload(course), follow=True)
    response = superuser_client.post(ADD_URL, data=valid_payload(other_course, name="Different plan"))
    assert response.status_code == 200
    assert PaymentPlan.objects.filter(code="bootcamp-3x").count() == 1


@pytest.mark.django_db
def test_negative_amount_is_rejected_via_admin(superuser_client, course):
    response = superuser_client.post(ADD_URL, data=valid_payload(course, installment_amount="-10.00"))
    assert response.status_code == 200
    assert not PaymentPlan.objects.exists()


@pytest.mark.django_db
def test_invalid_installment_count_is_rejected_via_admin(superuser_client, course):
    response = superuser_client.post(ADD_URL, data=valid_payload(course, installment_count=0))
    assert response.status_code == 200
    assert not PaymentPlan.objects.exists()


@pytest.mark.django_db
def test_invalid_access_policy_is_rejected_via_admin(superuser_client, course):
    response = superuser_client.post(ADD_URL, data=valid_payload(course, access_policy="MARK_PAID_MANUALLY"))
    assert response.status_code == 200
    assert not PaymentPlan.objects.exists()


@pytest.mark.django_db
def test_total_field_submitted_by_browser_is_ignored(superuser_client, course):
    """
    PaymentPlan has no editable 'total' field — the form has nothing to bind a
    browser-submitted total to, so it's silently ignored and computed_total is
    always installment_count * installment_amount server-side.
    """
    payload = valid_payload(course)
    payload["total"] = "1"  # not a real field; simulates a tampered/extraneous POST value
    superuser_client.post(ADD_URL, data=payload, follow=True)
    plan = PaymentPlan.objects.get(code="bootcamp-3x")
    assert plan.computed_total == Decimal("102000.00")


@pytest.mark.django_db
def test_editing_a_plan_persists_changes(superuser_client, course):
    superuser_client.post(ADD_URL, data=valid_payload(course), follow=True)
    plan = PaymentPlan.objects.get(code="bootcamp-3x")
    change_url = reverse("admin:payments_paymentplan_change", args=[plan.pk])

    updated = valid_payload(course, name="Bootcamp — renamed", installment_amount="40000.00")
    response = superuser_client.post(change_url, data=updated, follow=True)
    assert response.status_code == 200

    plan.refresh_from_db()
    assert plan.name == "Bootcamp — renamed"
    assert plan.installment_amount == Decimal("40000.00")


@pytest.mark.django_db
def test_activate_and_deactivate_via_changelist(superuser_client, course):
    superuser_client.post(ADD_URL, data=valid_payload(course, **{"is_active": ""}), follow=True)
    plan = PaymentPlan.objects.get(code="bootcamp-3x")
    assert plan.is_active is False

    response = superuser_client.post(CHANGELIST_URL, data={
        "form-TOTAL_FORMS": "1",
        "form-INITIAL_FORMS": "1",
        "form-MIN_NUM_FORMS": "0",
        "form-MAX_NUM_FORMS": "1000",
        "form-0-id": plan.pk,
        "form-0-is_active": "on",
        "form-0-display_order": 0,
        "_save": "Save",
    }, follow=True)
    assert response.status_code == 200
    plan.refresh_from_db()
    assert plan.is_active is True


@pytest.mark.django_db
def test_delete_is_not_permitted(superuser_client, course):
    """No delete action is exposed for Phase 1 — retiring a plan means deactivating it (see PaymentPlanAdmin.has_delete_permission)."""
    superuser_client.post(ADD_URL, data=valid_payload(course), follow=True)
    plan = PaymentPlan.objects.get(code="bootcamp-3x")
    delete_url = reverse("admin:payments_paymentplan_delete", args=[plan.pk])
    response = superuser_client.get(delete_url)
    assert response.status_code == 403
    assert PaymentPlan.objects.filter(pk=plan.pk).exists()


@pytest.mark.django_db
def test_add_form_renders_course_autocomplete_widget(superuser_client, course):
    """Reuses Django Admin's built-in autocomplete (enabled by apps/courses/admin.py::CourseAdmin.search_fields) — no new component built."""
    response = superuser_client.get(ADD_URL)
    assert response.status_code == 200
    assert b'class="admin-autocomplete' in response.content or b"autocomplete" in response.content.lower()


@pytest.mark.django_db
def test_changelist_shows_computed_total_column(superuser_client, course):
    superuser_client.post(ADD_URL, data=valid_payload(course), follow=True)
    response = superuser_client.get(CHANGELIST_URL)
    assert response.status_code == 200
    assert b"102,000.00 XAF" in response.content


# --- Organization/tenant isolation ---
#
# Known from repository (Phase 0): no Organization/Account/Tenant model exists
# anywhere in this codebase; GEMINI.md explicitly states "Multi-tenancy: Single
# account instance." Cross-organization course selection and organization
# isolation therefore have no scope boundary to test against in this repo as it
# stands — there is exactly one implicit organization, matching how
# TaraConfig/ClickFunnelsConfig are scoped today. If multi-tenancy is introduced
# later, these tests must be added then, alongside the model changes that make
# them meaningful.
