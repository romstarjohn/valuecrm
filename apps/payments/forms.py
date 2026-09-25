import uuid

from django import forms
from django.utils.text import slugify

from apps.courses.models import Course
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

    plan_id's queryset is scoped to the current product's active plans only
    (pass `plans=`) — a plan id belonging to a different product's checkout
    page is rejected by field validation the same way an inactive one is.
    """
    plan_id = forms.ModelChoiceField(
        queryset=PaymentPlan.objects.filter(is_active=True),
        widget=forms.RadioSelect,
        empty_label=None,
        error_messages={"invalid_choice": "Ce plan n’est plus disponible. Veuillez en choisir un autre."},
    )
    idempotency_key = forms.CharField(widget=forms.HiddenInput(), max_length=64)

    email = forms.EmailField(
        label="Adresse e-mail de réception",
        help_text="Pour recevoir votre confirmation et les informations d’accès.",
        widget=forms.EmailInput(attrs={"class": "form-control", "placeholder": "vous@exemple.com", "autocomplete": "email"}),
    )
    phone = forms.CharField(
        label="Téléphone", max_length=50,
        error_messages={"required": "Renseignez votre numéro de téléphone."},
        widget=forms.TextInput(attrs={"class": "form-control", "type": "tel", "autocomplete": "tel", "placeholder": "+237 600 000 000", "aria-describedby": "phone-help id_phone_error"}),
    )
    first_name = forms.CharField(
        label="Prénom", max_length=255,
        error_messages={"required": "Renseignez votre prénom."},
        widget=forms.TextInput(attrs={"class": "form-control", "autocomplete": "given-name", "placeholder": "Votre prénom"}),
    )
    last_name = forms.CharField(
        label="Nom", max_length=255,
        error_messages={"required": "Renseignez votre nom."},
        widget=forms.TextInput(attrs={"class": "form-control", "autocomplete": "family-name", "placeholder": "Votre nom"}),
    )

    def __init__(self, *args, plans=None, **kwargs):
        super().__init__(*args, **kwargs)
        if plans is not None:
            self.fields["plan_id"].queryset = plans

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()

    def clean_phone(self):
        phone = normalize_phone(self.cleaned_data["phone"])
        if not phone.lstrip("+").isdigit() or phone.count("+") > 1:
            raise forms.ValidationError("Saisissez un numéro de téléphone valide.")
        return phone

    @staticmethod
    def initial() -> dict:
        return {"idempotency_key": str(uuid.uuid4())}


class PaymentPlanForm(forms.ModelForm):
    """
    Staff-facing (apps/payments/staff_views.py) — creates/edits a PaymentPlan,
    the platform's "produit" (docs/PRODUCT_CADRAGE_PMI.md §4: one produit per
    cours, always tied to an existing imported course, never the reverse).
    `code` auto-generates from `name` when left blank — the same convenience
    apps.courses.models.Course.save() already gives its own slug, so creating
    a plan never blocks on typing a stable internal identifier by hand.
    """

    class Meta:
        model = PaymentPlan
        fields = [
            "course", "name", "code", "description",
            "installment_count", "installment_amount", "installment_interval_days",
            "access_policy", "is_active", "display_order",
        ]
        widgets = {
            "course": forms.Select(attrs={"class": "form-select"}),
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Ex. : Locking Professionnel — 3 versements"}),
            "code": forms.TextInput(attrs={"class": "form-control", "placeholder": "Généré automatiquement si laissé vide"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "installment_count": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "installment_amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0.01"}),
            "installment_interval_days": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "access_policy": forms.Select(attrs={"class": "form-select"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "display_order": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
        }
        help_texts = {
            "installment_count": "1 pour un paiement unique, ou le nombre de versements.",
            "installment_amount": "Montant par versement — le total est calculé, jamais saisi directement.",
            "is_active": "Un plan inactif n'apparaît pas sur le lien de paiement public.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["course"].queryset = Course.objects.order_by("name")
        self.fields["code"].required = False

    def clean_code(self):
        code = (self.cleaned_data.get("code") or "").strip()
        if code:
            return code
        # `name` is cleaned before `code` (Meta.fields order), so it's
        # already in cleaned_data here whether this is a create or an edit.
        base = slugify(self.cleaned_data.get("name", "")) or "plan"
        candidate = base
        suffix = 2
        qs = PaymentPlan.objects.exclude(pk=self.instance.pk)
        while qs.filter(code=candidate).exists():
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate
