from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from django.utils.translation import override
from django_ratelimit.decorators import ratelimit
from apps.courses.models import CheckoutOffer, Course

from .forms import CheckoutContactForm
from .models import PaymentAttempt, PaymentPlan
from .services import NOT_STARTED, CheckoutConfigurationError, CheckoutError, CheckoutService


@override("fr")
@ratelimit(key="ip", rate="20/m", block=True)
def shop_index(request):
    """
    Product listing — every course with at least one active PaymentPlan gets
    a card here, linking to its own /paiement/<course.slug>/ page. Marketing
    content is that course's own CheckoutOffer if it has one, else the shared
    default (same inheritance the checkout page itself uses).
    """
    courses = (
        Course.objects.filter(payment_plans__is_active=True)
        .select_related("checkout_offer")
        .distinct()
        .order_by("name")
    )
    default_offer = CheckoutOffer.objects.filter(is_default=True).first() if courses else None
    products = []
    for course in courses:
        offer = getattr(course, "checkout_offer", None) or default_offer
        if not offer:
            continue
        cheapest_plan = (
            PaymentPlan.objects.filter(course=course, is_active=True)
            .order_by("installment_amount")
            .first()
        )
        products.append({"course": course, "offer": offer, "starting_at": cheapest_plan})
    return render(request, "payments/shop_index.html", {"products": products})


@override("fr")
@ratelimit(key="ip", rate="20/m", block=True)
def checkout_start(request, slug):
    """
    Single-product checkout — the URL identifies exactly one course
    (e.g. /paiement/cheveux-crepus-longs-et-libres/). Its active PaymentPlans
    are rendered as radio options directly on this page (no per-plan URL).
    GET renders plan choices + the contact form; POST resolves/creates the
    guest Contact and runs CheckoutService.start_checkout() for whichever
    plan was selected.

    Marketing content is the course's own CheckoutOffer if it has one, else
    the shared default template — same inheritance as before this page had
    a per-course URL.

    The submitted plan_id is re-validated server-side by
    CheckoutContactForm's ModelChoiceField (scoped to this course's
    is_active=True plans) — never trusted as-is; a tampered/stale/inactive/
    other-course id simply fails validation and the form is re-rendered
    with an error, same guarantee the old get_object_or_404(is_active=True)
    gave when the plan lived in the URL.
    """
    course = get_object_or_404(Course.objects.select_related("checkout_offer"), slug=slug)
    default_offer = CheckoutOffer.objects.filter(is_default=True).prefetch_related("benefits").first()
    offer = getattr(course, "checkout_offer", None) or default_offer
    plans = (
        PaymentPlan.objects.filter(course=course, is_active=True)
        .order_by("display_order", "name")
    )

    if request.method == "POST":
        form = CheckoutContactForm(request.POST, plans=plans)
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
                    "message": "La commande est temporairement indisponible. Veuillez réessayer dans quelques instants.",
                }, status=503)
            except CheckoutError:
                return render(request, "payments/checkout_error.html", {
                    "message": "Nous n’avons pas pu traiter votre demande. Veuillez réessayer.",
                }, status=400)

            if result.checkout_url:
                return redirect(result.checkout_url)
            if result.status == NOT_STARTED:
                return _render_not_started(request, result.signed_reference)
            return redirect("payments:checkout_status", token=result.signed_reference)
    else:
        form = CheckoutContactForm(initial=CheckoutContactForm.initial(), plans=plans)
        first_plan = plans.first()
        selected_plan_id = str(first_plan.id) if first_plan else ""

    selected_plan = next((plan for plan in plans if str(plan.id) == selected_plan_id), None)
    checkout_title = offer.title if offer else course.name
    for plan in plans:
        plan.checkout_title = checkout_title
    return render(request, "payments/checkout_form.html", {
        "plans": plans, "form": form, "selected_plan_id": selected_plan_id,
        "selected_plan": selected_plan,
        "offer": offer, "course": course, "checkout_title": checkout_title,
        "course_presentations": [{"course": course, "offer": offer}],
    })


@override("fr")
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
    except CheckoutError:
        return render(request, "payments/checkout_status.html", {"error": "Ce lien de commande est invalide ou a expiré."}, status=400)

    # The installment the customer most recently tried to pay (installment 2+
    # when coming back from a reminder link), not always installment 1.
    latest = PaymentAttempt.objects.filter(installment__order=order).order_by("-created_at").first()
    attempt = None
    if latest:
        # Within that installment a succeeded attempt wins over any newer one: a late verified
        # success on an older attempt EXPIREs the newer one (PaymentCreditService._reopen_for_late_success).
        attempt = (
            latest.installment.payment_attempts.filter(status=PaymentAttempt.Status.SUCCEEDED).first()
            or latest
        )
    display_status = _map_display_status(attempt)
    if display_status == NOT_STARTED:
        return _render_not_started(request, token, order=order)

    return render(request, "payments/checkout_status.html", {
        "order": order,
        "display_status": display_status,
        "token": token,
    })


@override("fr")
@require_POST
@ratelimit(key="ip", rate="10/m", block=True)
def checkout_status_refresh(request, token):
    """
    Customer-triggered "check again" for the status page — never itself
    reports or trusts a status (see CheckoutService.request_status_refresh's
    docstring for why), it only asks Tara to redeliver a possibly-lost
    webhook and redirects back to the same read-only status page, which will
    reflect whatever the webhook path itself ends up doing with that
    redelivery. POST-only (real, if best-effort, side effect against a
    third-party API) and rate-limited same as the other public endpoints.
    """
    try:
        order = CheckoutService().resolve_signed_reference(token)
    except CheckoutError:
        return render(request, "payments/checkout_status.html", {"error": "Ce lien de commande est invalide ou a expiré."}, status=400)

    CheckoutService().request_status_refresh(order)
    messages.info(
        request,
        "Nous avons demandé une mise à jour de votre paiement. Cela peut prendre un instant ; cette page continuera à vérifier automatiquement.",
    )
    return redirect("payments:checkout_status", token=token)


def _render_not_started(request, token, order=None):
    """B: nothing was charged — say so plainly and offer a one-click retry (no retyping)."""
    if order is None:
        order = CheckoutService().resolve_signed_reference(token)
    return render(request, "payments/checkout_not_started.html", {"order": order, "token": token})


@override("fr")
@require_POST
@ratelimit(key="ip", rate="10/m", block=True)
def checkout_retry(request, token):
    """B: "Réessayer" — starts the payment again for the same order (same verified path as the first try)."""
    try:
        order = CheckoutService().resolve_signed_reference(token)
        result = CheckoutService().pay_next_installment(order)
    except CheckoutConfigurationError:
        return render(request, "payments/checkout_error.html", {
            "message": "Le paiement est temporairement indisponible. Veuillez réessayer dans quelques instants.",
        }, status=503)
    except CheckoutError:
        return render(request, "payments/checkout_status.html", {"error": "Ce lien de commande est invalide ou a expiré."}, status=400)
    if result.checkout_url:
        return redirect(result.checkout_url)
    if result.status == NOT_STARTED:
        return _render_not_started(request, result.signed_reference, order=order)
    return redirect("payments:checkout_status", token=result.signed_reference)


def _map_display_status(attempt) -> str:
    if attempt is None:
        return NOT_STARTED
    if attempt.status == PaymentAttempt.Status.UNKNOWN and not attempt.general_link:
        return NOT_STARTED  # Tara timed out creating the link: the customer never got one
    return {
        PaymentAttempt.Status.CREATED: NOT_STARTED,
        PaymentAttempt.Status.LINK_CREATED: "link_ready",
        PaymentAttempt.Status.PENDING: "verification_pending",
        PaymentAttempt.Status.SUCCEEDED: "payment_confirmed",
        PaymentAttempt.Status.FAILED: "failed",
        PaymentAttempt.Status.EXPIRED: "failed",
        PaymentAttempt.Status.UNKNOWN: "verification_pending",
    }.get(attempt.status, "error")


@override("fr")
@never_cache
@ratelimit(key="ip", rate="20/m", block=True)
def installment_pay(request, token):
    """
    Personal payment link for a later installment (reminder e-mails, or a link
    staff copy from the sale page). GET only shows what is due — it never
    calls Tara, because e-mail security scanners pre-open links. The POST
    ("Payer") starts the same verified Tara payment as the first checkout.
    """
    checkout_service = CheckoutService()
    try:
        order = checkout_service.resolve_installment_payment_token(token)
    except CheckoutError as e:
        return render(request, "payments/installment_pay.html", {"error": str(e)}, status=400)

    installment = checkout_service.next_outstanding_installment(order)
    context = {"order": order, "installment": installment, "token": token}
    if installment is None:
        return render(request, "payments/installment_pay.html", {**context, "all_paid": True})

    if request.method == "POST":
        try:
            result = checkout_service.pay_next_installment(order)
        except CheckoutConfigurationError:
            return render(request, "payments/checkout_error.html", {
                "message": "Le paiement est temporairement indisponible. Veuillez réessayer dans quelques instants.",
            }, status=503)
        except CheckoutError as e:
            return render(request, "payments/installment_pay.html", {**context, "error": str(e)}, status=400)
        if result.checkout_url:
            return redirect(result.checkout_url)
        return redirect("payments:checkout_status", token=result.signed_reference)

    return render(request, "payments/installment_pay.html", context)

