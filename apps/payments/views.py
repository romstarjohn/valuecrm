from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django_ratelimit.decorators import ratelimit

from .forms import CheckoutContactForm
from .models import PaymentAttempt, PaymentPlan
from .services import CheckoutConfigurationError, CheckoutError, CheckoutService


def plan_list(request):
    """
    Public checkout entry point — active PaymentPlans only. Every displayed
    field (name, description, course, currency, installment count/amount,
    computed total, access policy) is read directly from the server-side
    PaymentPlan/Course records; nothing here is browser-supplied.
    """
    plans = (
        PaymentPlan.objects.filter(is_active=True)
        .select_related("course")
        .order_by("display_order", "name")
    )
    return render(request, "payments/plan_list.html", {"plans": plans})


@ratelimit(key="ip", rate="20/m", block=True)
def checkout_start(request, plan_id):
    """
    GET renders the contact form for an active plan; POST resolves/creates
    the guest Contact and runs CheckoutService.start_checkout(). The plan_id
    in the URL and the hidden plan_id form field are both re-validated
    server-side against PaymentPlan.is_active — never trusted as-is.
    """
    plan = get_object_or_404(PaymentPlan.objects.select_related("course"), pk=plan_id, is_active=True)

    if request.method == "POST":
        form = CheckoutContactForm(request.POST)
        if form.is_valid() and form.cleaned_data["plan_id"] == plan.id:
            checkout_service = CheckoutService()
            contact = checkout_service.resolve_guest_contact(
                email=form.cleaned_data["email"],
                phone=form.cleaned_data["phone"],
                first_name=form.cleaned_data["first_name"],
                last_name=form.cleaned_data["last_name"],
            )
            try:
                result = checkout_service.start_checkout(
                    contact, plan.id, form.cleaned_data["idempotency_key"],
                )
            except CheckoutConfigurationError:
                return render(request, "payments/checkout_error.html", {
                    "message": "Checkout is temporarily unavailable. Please try again shortly.",
                }, status=503)
            except CheckoutError:
                return render(request, "payments/checkout_error.html", {
                    "message": "We couldn't process your request. Please try again.",
                }, status=400)

            if result.checkout_url:
                return redirect(result.checkout_url)
            return redirect("payments:checkout_status", token=result.signed_reference)
    else:
        form = CheckoutContactForm(initial=CheckoutContactForm.initial_for_plan(plan))

    return render(request, "payments/checkout_form.html", {"plan": plan, "form": form})


@never_cache
@ratelimit(key="ip", rate="30/m", block=True)
def checkout_status(request, token):
    """
    Return/status page. Resolves the order ONLY via the opaque signed,
    expiring token in the URL path — never via a bare pk/UUID, and never via
    query-string values, which are deliberately never read here (a customer
    or an attacker appending ?status=success&amount=0 has no effect on what's
    displayed). Never mutates any payment/order/installment state.
    """
    try:
        order = CheckoutService().resolve_signed_reference(token)
    except CheckoutError as e:
        return render(request, "payments/checkout_status.html", {"error": str(e)}, status=400)

    installment = order.installments.order_by("sequence").first()
    attempt = installment.payment_attempts.order_by("-created_at").first() if installment else None
    display_status = _map_display_status(attempt)

    return render(request, "payments/checkout_status.html", {
        "order": order,
        "display_status": display_status,
    })


def _map_display_status(attempt) -> str:
    if attempt is None:
        return "awaiting_payment"
    return {
        PaymentAttempt.Status.CREATED: "awaiting_payment",
        PaymentAttempt.Status.LINK_CREATED: "link_ready",
        PaymentAttempt.Status.PENDING: "verification_pending",
        PaymentAttempt.Status.SUCCEEDED: "payment_confirmed",
        PaymentAttempt.Status.FAILED: "failed",
        PaymentAttempt.Status.EXPIRED: "failed",
        PaymentAttempt.Status.UNKNOWN: "verification_pending",
    }.get(attempt.status, "error")
