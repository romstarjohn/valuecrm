from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django_ratelimit.decorators import ratelimit

from .forms import CheckoutContactForm
from .models import PaymentAttempt, PaymentPlan
from .services import CheckoutConfigurationError, CheckoutError, CheckoutService


@ratelimit(key="ip", rate="20/m", block=True)
def checkout_start(request):
    """
    Single-page checkout — active PaymentPlans are rendered as radio options
    directly on this page (no per-plan URL). GET renders plan choices + the
    contact form; POST resolves/creates the guest Contact and runs
    CheckoutService.start_checkout() for whichever plan was selected.

    The submitted plan_id is re-validated server-side by
    CheckoutContactForm's ModelChoiceField (scoped to is_active=True) —
    never trusted as-is; a tampered/stale/inactive id simply fails
    validation and the form is re-rendered with an error, same guarantee
    the old get_object_or_404(is_active=True) gave when the plan lived in
    the URL.
    """
    plans = (
        PaymentPlan.objects.filter(is_active=True)
        .select_related("course")
        .order_by("display_order", "name")
    )

    if request.method == "POST":
        form = CheckoutContactForm(request.POST)
        selected_plan_id = request.POST.get("plan_id", "")
        if form.is_valid():
            plan = form.cleaned_data["plan_id"]
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
        form = CheckoutContactForm(initial=CheckoutContactForm.initial())
        first_plan = plans.first()
        selected_plan_id = str(first_plan.id) if first_plan else ""

    return render(request, "payments/checkout_form.html", {
        "plans": plans, "form": form, "selected_plan_id": selected_plan_id,
    })


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
