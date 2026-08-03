from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission, User
from django.test import Client
from django.urls import reverse

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import Order, PaymentAttempt, PaymentPlan
from apps.payments.services import OrderService, PaymentAttemptService, PaymentCreditService

pytestmark = pytest.mark.django_db

LIST_URL = reverse("operations:order_list")


def make_order(email, installment_count=1, installment_amount=Decimal("100000.00"), access_policy=None):
    course = Course.objects.create(cf_course_id=f"crs_{email}", name="Bootcamp", workspace_id="ws_1")
    kwargs = dict(
        code=f"plan_{email}", name="Bootcamp", course=course,
        installment_count=installment_count, installment_amount=installment_amount, is_active=True,
    )
    if access_policy is not None:
        kwargs["access_policy"] = access_policy
    plan = PaymentPlan.objects.create(**kwargs)
    contact = Contact.objects.create(email=email, first_name="Order", last_name="Search")
    order, _ = OrderService().create_order(contact, plan.id, f"idem-{email}")
    return order


def test_anonymous_redirected(client):
    response = client.get(LIST_URL)
    assert response.status_code == 302


def test_staff_without_view_permission_forbidden():
    user = User.objects.create_user(username="noviewperm", password="x", is_staff=True)
    client = Client()
    client.login(username="noviewperm", password="x")
    response = client.get(LIST_URL)
    assert response.status_code == 403


def test_staff_with_view_permission_succeeds():
    user = User.objects.create_user(username="hasviewperm", password="x", is_staff=True)
    user.user_permissions.add(Permission.objects.get(codename="view_order"))
    client = Client()
    client.login(username="hasviewperm", password="x")
    response = client.get(LIST_URL)
    assert response.status_code == 200


def test_search_by_reference(ops_client):
    order = make_order("search-ref@example.com")
    response = ops_client.get(LIST_URL, {"q": str(order.reference)})
    assert list(response.context["page_obj"]) == [order]


def test_search_by_customer_email(ops_client):
    order = make_order("search-email-match@example.com")
    make_order("other-unrelated@example.com")
    response = ops_client.get(LIST_URL, {"q": "search-email-match"})
    results = list(response.context["page_obj"])
    assert len(results) == 1
    assert results[0].pk == order.pk


def test_filter_by_status(ops_client):
    pending = make_order("filter-status-pending@example.com")
    completed = make_order("filter-status-completed@example.com")
    installment = completed.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    attempt = PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentCreditService().apply_verified_success(attempt.id)

    response = ops_client.get(LIST_URL, {"status": "COMPLETED"})
    results = list(response.context["page_obj"])
    assert completed in results
    assert pending not in results


def test_invalid_status_filter_value_ignored_not_injected(ops_client):
    make_order("filter-invalid-status@example.com")
    response = ops_client.get(LIST_URL, {"status": "'; DROP TABLE--"})
    assert response.status_code == 200  # validated against choices, not passed through raw


def test_filter_by_plan(ops_client):
    order = make_order("filter-plan@example.com")
    other = make_order("filter-plan-other@example.com")
    response = ops_client.get(LIST_URL, {"plan": str(order.plan_id)})
    results = list(response.context["page_obj"])
    assert order in results
    assert other not in results


def test_filter_by_access_eligible(ops_client):
    eligible = make_order("filter-access-eligible@example.com")
    installment = eligible.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    attempt = PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentCreditService().apply_verified_success(attempt.id)
    not_eligible = make_order("filter-access-not-eligible@example.com")

    response = ops_client.get(LIST_URL, {"access": "eligible"})
    results = list(response.context["page_obj"])
    assert eligible in results
    assert not_eligible not in results


def test_filter_by_manual_disposition(ops_client):
    order = make_order("filter-disposition@example.com")
    order.manual_disposition = Order.ManualDisposition.NEEDS_REVIEW
    order.save()
    other = make_order("filter-disposition-other@example.com")

    response = ops_client.get(LIST_URL, {"disposition": "NEEDS_REVIEW"})
    results = list(response.context["page_obj"])
    assert order in results
    assert other not in results


def test_filter_by_needs_review(ops_client):
    order = make_order("filter-needs-review@example.com")
    installment = order.installments.get()
    attempt = PaymentAttemptService().create_attempt(installment)
    attempt = PaymentAttemptService().transition(attempt, PaymentAttempt.Status.LINK_CREATED)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.PENDING)
    PaymentAttemptService().transition(attempt, PaymentAttempt.Status.UNKNOWN)
    clean_order = make_order("filter-needs-review-clean@example.com")

    response = ops_client.get(LIST_URL, {"needs_review": "1"})
    results = list(response.context["page_obj"])
    assert order in results
    assert clean_order not in results


def test_pagination_preserves_filters(ops_client):
    for i in range(25):
        make_order(f"paginate-{i}@example.com", installment_amount=Decimal("50000.00"))
    response = ops_client.get(LIST_URL, {"status": "PENDING", "page": "2"})
    assert response.status_code == 200
    assert response.context["page_obj"].number == 2
    assert response.context["querystring"] == "status=PENDING"
    content = response.content.decode()
    assert "status=PENDING" in content  # pagination links carry the filter forward


def test_pagination_bounded_page_size(ops_client):
    for i in range(25):
        make_order(f"bound-{i}@example.com")
    response = ops_client.get(LIST_URL)
    assert len(response.context["page_obj"]) <= 20


def test_query_count_does_not_scale_with_order_count(ops_client):
    from django.db import connection, reset_queries
    from django.test.utils import override_settings

    for i in range(3):
        make_order(f"qc-few-{i}@example.com")

    with override_settings(DEBUG=True):
        reset_queries()
        ops_client.get(LIST_URL)
        few_queries = len(connection.queries)

        for i in range(15):
            make_order(f"qc-many-{i}@example.com")

        reset_queries()
        ops_client.get(LIST_URL)
        many_queries = len(connection.queries)

    assert many_queries < few_queries + 10, f"query count grew from {few_queries} to {many_queries} — looks like an N+1"
