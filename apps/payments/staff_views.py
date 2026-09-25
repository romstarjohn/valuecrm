"""
Staff-facing Produits & Plans management — deliberately separate from
views.py (the public guest checkout, apps/payments/urls.py under
/paiement/). Mounted at /plans/ (apps/payments/staff_urls.py) so it lives
under the portal's own sidebar/topbar, never the public checkout chrome.
"""
from itertools import groupby

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from apps.courses.models import Course
from .forms import PaymentPlanForm
from .models import PaymentPlan


@login_required
def plan_list(request):
    """
    Grouped by course, not a flat table of plans — a plan is never a peer
    of its course, it's part of it (docs/PRODUCT_CADRAGE_PMI.md §4: one
    produit per cours, several plans may belong to that same produit).
    Courses with no plan yet are still listed, with a direct "Créer un plan"
    action — the point of this screen is "what needs a plan," not just
    "what plans exist."
    """
    if not request.user.has_perm("payments.view_paymentplan"):
        raise PermissionDenied

    plans = PaymentPlan.objects.order_by("course__name", "display_order", "name")
    plans_by_course_id = {}
    for course_id, course_plans in groupby(plans, key=lambda p: p.course_id):
        plans_by_course_id[course_id] = list(course_plans)

    groups = [
        {"course": course, "plans": plans_by_course_id.get(course.pk, [])}
        for course in Course.objects.order_by("name")
    ]

    context = {
        "groups": groups,
        "can_add": request.user.has_perm("payments.add_paymentplan"),
        "can_edit": request.user.has_perm("payments.change_paymentplan"),
    }
    return render(request, "payments/staff/plan_list.html", context)


@login_required
def plan_create(request):
    if not request.user.has_perm("payments.add_paymentplan"):
        raise PermissionDenied

    initial = {}
    course_id = request.GET.get("course_id", "")
    if course_id.isdigit():
        initial["course"] = course_id

    if request.method == "POST":
        form = PaymentPlanForm(request.POST)
        if form.is_valid():
            plan = form.save()
            messages.success(request, f"Plan « {plan.name} » créé pour {plan.course.name}.")
            return redirect("plans:list")
    else:
        form = PaymentPlanForm(initial=initial)

    return render(request, "payments/staff/plan_form.html", {
        "form": form, "title": "Nouveau plan de paiement", "is_create": True,
    })


@login_required
def plan_update(request, pk):
    plan = get_object_or_404(PaymentPlan.objects.select_related("course"), pk=pk)
    if not request.user.has_perm("payments.change_paymentplan"):
        raise PermissionDenied

    if request.method == "POST":
        form = PaymentPlanForm(request.POST, instance=plan)
        if form.is_valid():
            plan = form.save()
            messages.success(request, f"Plan « {plan.name} » mis à jour.")
            return redirect("plans:list")
    else:
        form = PaymentPlanForm(instance=plan)

    return render(request, "payments/staff/plan_form.html", {
        "form": form, "plan": plan, "title": f"Modifier {plan.name}", "is_create": False,
    })
