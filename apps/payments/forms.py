import uuid

from django import forms

from .models import PaymentPlan
from .services import normalize_phone


class CheckoutContactForm(forms.Form):
    """
    Guest checkout form — the browser submits only the selected plan
    identifier and permitted customer information (docs/TARA_INTEGRATION_PROJECT.md
    Phase 5). No field here accepts price, currency, course, installment
    count, total, interval, or access policy — CheckoutService always
    re-derives those server-side from the authoritative PaymentPlan.

    plan_id is a ModelChoiceField scoped to active plans only — the browser
    picks one of the rendered radio options, but a submitted id for an
    inactive (or nonexistent) plan is rejected by field validation itself,
    the same guarantee the old get_object_or_404(is_active=True) gave when
    the plan lived in the URL instead of the form body.

    idempotency_key is generated once when the form is first rendered (GET)
    and round-tripped as a hidden field, so a double-click/back-button
    resubmission carries the same key rather than a fresh one each time.
    """
    plan_id = forms.ModelChoiceField(
        queryset=PaymentPlan.objects.filter(is_active=True),
        widget=forms.RadioSelect,
        empty_label=None,
        error_messages={"invalid_choice": "This plan is no longer available. Please choose another."},
    )
    idempotency_key = forms.CharField(widget=forms.HiddenInput(), max_length=64)

    email = forms.EmailField(
        label="Email address",
        widget=forms.EmailInput(attrs={"class": "form-control", "placeholder": "you@example.com"}),
    )
    phone = forms.CharField(
        label="Phone number", required=False, max_length=50,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "+237600000000"}),
    )
    first_name = forms.CharField(
        label="First name", required=False, max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    last_name = forms.CharField(
        label="Last name", required=False, max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()

    def clean_phone(self):
        return normalize_phone(self.cleaned_data.get("phone", ""))

    @staticmethod
    def initial() -> dict:
        return {"idempotency_key": str(uuid.uuid4())}
