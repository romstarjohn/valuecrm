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
    Staff-facing "formule de prix" of one course (docs/UI_VOCABULARY.md).
    Asks the question a non-technical user can answer — "comment le client
    paie-t-il ?" — instead of raw installment fields: "en une fois" forces
    installment_count=1, "en plusieurs fois" asks for the number and the
    rhythm. `code` is always generated; display_order is optional ("Options
    avancées"). Every PaymentPlan invariant still comes from the model
    (PaymentPlan.clean(), field validators).

    Pass `course=` when the course is known (opened from an Offre page, or
    editing): it is then fixed instead of offered as a choice.
    """

    ONCE, SEVERAL = "once", "several"
    payment_mode = forms.ChoiceField(
        label="Comment le client paie-t-il ?",
        choices=[(ONCE, "En une fois"), (SEVERAL, "En plusieurs fois")],
        widget=forms.RadioSelect,
        initial=ONCE,
    )

    class Meta:
        model = PaymentPlan
        fields = [
            "course", "name", "description",
            "installment_count", "installment_amount", "installment_interval_days",
            "access_policy", "is_active", "display_order",
        ]
        labels = {
            "course": "Formation",
            "name": "Nom de la formule (visible par le client)",
            "description": "Précision affichée sous le prix (facultatif)",
            "installment_count": "Nombre de paiements",
            "installment_amount": "Montant de chaque paiement (XAF)",
            "installment_interval_days": "Nombre de jours entre deux paiements",
            "access_policy": "Quand le client reçoit-il l'accès à la formation ?",
            "is_active": "En vente — visible sur le lien de paiement",
            "display_order": "Position dans la liste des formules",
        }
        widgets = {
            "course": forms.Select(attrs={"class": "form-select"}),
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Ex. : Paiement en une fois"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Ex. : Le plus simple — accès immédiat."}),
            "installment_count": forms.NumberInput(attrs={"class": "form-control", "min": 2}),
            "installment_amount": forms.NumberInput(attrs={"class": "form-control", "step": "1", "min": "1", "placeholder": "Ex. : 20000"}),
            "installment_interval_days": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "access_policy": forms.RadioSelect,
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "display_order": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
        }
        help_texts = {
            "installment_interval_days": "Ex. : 30 pour un paiement par mois. Le premier paiement se fait à la commande.",
            "is_active": "Décochez pour masquer cette formule sans la supprimer. Les ventes déjà faites ne changent pas.",
            "display_order": "0 = en premier. Laissez 0 si l'ordre vous est égal.",
        }

    def __init__(self, *args, course=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fixed_course = course or (self.instance.course if self.instance.pk else None)
        if self.fixed_course is not None:
            del self.fields["course"]
            self.instance.course = self.fixed_course
        else:
            self.fields["course"].queryset = Course.objects.order_by("name")
        self.fields["access_policy"].choices = [
            (PaymentPlan.AccessPolicy.FIRST_INSTALLMENT, "Dès le premier paiement"),
            (PaymentPlan.AccessPolicy.FULL_PAYMENT, "Une fois tout payé"),
        ]
        self.fields["installment_count"].required = False
        self.fields["installment_interval_days"].required = False
        self.fields["display_order"].required = False
        if self.instance.pk and self.instance.installment_count > 1:
            self.initial["payment_mode"] = self.SEVERAL

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("payment_mode") == self.ONCE:
            cleaned["installment_count"] = 1
            if not cleaned.get("installment_interval_days"):
                cleaned["installment_interval_days"] = self.instance.installment_interval_days or 30
        else:
            count = cleaned.get("installment_count")
            if not count or count < 2:
                self.add_error("installment_count", "Indiquez au moins 2 paiements (sinon choisissez « En une fois »).")
            if not cleaned.get("installment_interval_days"):
                self.add_error("installment_interval_days", "Indiquez le nombre de jours entre deux paiements.")
        if cleaned.get("display_order") is None:
            cleaned["display_order"] = self.instance.display_order or 0
        return cleaned

    def _post_clean(self):
        # The forced/defaulted values from clean() must reach the instance before model validation.
        for field in ("installment_count", "installment_interval_days", "display_order"):
            if field in self.cleaned_data and self.cleaned_data[field] is not None:
                setattr(self.instance, field, self.cleaned_data[field])
        super()._post_clean()

    def save(self, commit=True):
        plan = super().save(commit=False)
        if not plan.code:
            base = slugify(plan.name) or "formule"
            candidate, suffix = base, 2
            while PaymentPlan.objects.exclude(pk=plan.pk).filter(code=candidate).exists():
                candidate = f"{base}-{suffix}"
                suffix += 1
            plan.code = candidate
        if commit:
            plan.save()
        return plan
