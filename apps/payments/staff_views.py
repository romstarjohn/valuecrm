"""
Staff-facing Produits & Plans management — deliberately separate from
views.py (the public guest checkout, apps/payments/urls.py under
/paiement/). Mounted at /plans/ (apps/payments/staff_urls.py) so it lives
under the portal's own sidebar/topbar, never the public checkout chrome.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from apps.courses.models import Course
from .forms import PaymentPlanForm
from .models import PaymentPlan


@login_required
def plan_list(request):
    """Former "Produits & Plans" list — prices now live on each Offre page (courses:list)."""
    if not request.user.has_perm("payments.view_paymentplan"):
        raise PermissionDenied
    return redirect("courses:list")


def _back_to_offer(plan):
    return redirect("courses:detail", cf_course_id=plan.course.cf_course_id)


@login_required
def plan_create(request):
    if not request.user.has_perm("payments.add_paymentplan"):
        raise PermissionDenied

    course = None
    course_id = request.GET.get("course_id", "")
    if course_id.isdigit():
        course = get_object_or_404(Course, pk=int(course_id))

    if request.method == "POST":
        form = PaymentPlanForm(request.POST, course=course)
        if form.is_valid():
            plan = form.save()
            messages.success(request, f"Formule « {plan.name} » ajoutée à {plan.course.name}.")
            return _back_to_offer(plan)
    else:
        form = PaymentPlanForm(course=course)

    return render(request, "payments/staff/plan_form.html", {
        "form": form, "course": course,
        "title": f"Nouvelle formule de prix — {course.name}" if course else "Nouvelle formule de prix",
        "is_create": True,
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
            messages.success(request, f"Formule « {plan.name} » enregistrée.")
            return _back_to_offer(plan)
    else:
        form = PaymentPlanForm(instance=plan)

    return render(request, "payments/staff/plan_form.html", {
        "form": form, "plan": plan, "course": plan.course,
        "title": f"Formule de prix — {plan.name}", "is_create": False,
    })
