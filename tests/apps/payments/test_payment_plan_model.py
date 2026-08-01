from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError

from apps.courses.models import Course
from apps.payments.models import PaymentPlan


@pytest.fixture
def course(db):
    return Course.objects.create(cf_course_id="crs_1", name="Bootcamp", workspace_id="ws_1")


def make_plan(course, **overrides):
    defaults = dict(
        code="bootcamp-3x",
        name="Bootcamp — 3 installments",
        course=course,
        currency=PaymentPlan.Currency.XAF,
        installment_count=3,
        installment_amount=Decimal("34000.00"),
        installment_interval_days=30,
        access_policy=PaymentPlan.AccessPolicy.FULL_PAYMENT,
    )
    defaults.update(overrides)
    return PaymentPlan(**defaults)


@pytest.mark.django_db
def test_computed_total_is_derived_not_stored(course):
    plan = make_plan(course, installment_count=3, installment_amount=Decimal("34000.00"))
    plan.full_clean()
    plan.save()
    assert plan.computed_total == Decimal("102000.00")

    # Editing pricing after save recomputes deterministically — no stale/cached total.
    plan.installment_amount = Decimal("40000.00")
    assert plan.computed_total == Decimal("120000.00")


@pytest.mark.django_db
def test_zero_amount_is_rejected(course):
    plan = make_plan(course, installment_amount=Decimal("0.00"))
    with pytest.raises(ValidationError):
        plan.full_clean()


@pytest.mark.django_db
def test_negative_amount_is_rejected(course):
    plan = make_plan(course, installment_amount=Decimal("-100.00"))
    with pytest.raises(ValidationError):
        plan.full_clean()


@pytest.mark.django_db
def test_zero_installment_count_is_rejected(course):
    plan = make_plan(course, installment_count=0)
    with pytest.raises(ValidationError):
        plan.full_clean()


@pytest.mark.django_db
def test_multi_installment_requires_positive_interval(course):
    plan = make_plan(course, installment_count=3, installment_interval_days=0)
    with pytest.raises(ValidationError):
        plan.full_clean()


@pytest.mark.django_db
def test_single_installment_ignores_zero_interval(course):
    plan = make_plan(course, installment_count=1, installment_interval_days=0)
    plan.full_clean()  # should not raise


@pytest.mark.django_db
def test_duplicate_plan_code_is_rejected(course):
    make_plan(course, code="dup-code").save()
    dup = make_plan(course, code="dup-code", name="Different name")
    with pytest.raises(ValidationError):
        dup.full_clean()


@pytest.mark.django_db
def test_invalid_currency_is_rejected(course):
    plan = make_plan(course, currency="USD")
    with pytest.raises(ValidationError):
        plan.full_clean()


@pytest.mark.django_db
def test_invalid_access_policy_is_rejected(course):
    plan = make_plan(course, access_policy="MARK_PAID_MANUALLY")
    with pytest.raises(ValidationError):
        plan.full_clean()


@pytest.mark.django_db
def test_course_deletion_is_protected_once_a_plan_references_it(course):
    make_plan(course).save()
    with pytest.raises(ProtectedError):
        course.delete()


@pytest.mark.django_db
def test_editing_a_plan_does_not_affect_other_plans(course):
    """
    No Order model exists yet (Phase 3) to snapshot terms at purchase time, so
    for Phase 1 "editing a plan affects future orders only" reduces to: editing
    one plan must not mutate any other persisted PaymentPlan row.
    """
    plan_a = make_plan(course, code="plan-a", name="Plan A", installment_amount=Decimal("10000.00"))
    plan_a.save()
    plan_b = make_plan(course, code="plan-b", name="Plan B", installment_amount=Decimal("20000.00"))
    plan_b.save()

    plan_a.installment_amount = Decimal("99999.00")
    plan_a.save()

    plan_b.refresh_from_db()
    assert plan_b.installment_amount == Decimal("20000.00")


@pytest.mark.django_db
def test_multiple_plans_may_be_active_simultaneously(course):
    """Unlike TaraConfig/ClickFunnelsConfig's singleton is_active, PaymentPlan.is_active is not exclusive."""
    plan_a = make_plan(course, code="plan-a", is_active=True)
    plan_a.full_clean()
    plan_a.save()
    plan_b = make_plan(course, code="plan-b", is_active=True)
    plan_b.full_clean()
    plan_b.save()

    plan_a.refresh_from_db()
    assert plan_a.is_active is True
    assert plan_b.is_active is True
