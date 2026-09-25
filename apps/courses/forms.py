from django import forms
from django.forms import inlineformset_factory
from django.utils.text import slugify

from .models import CheckoutBenefit, CheckoutOffer, Course


class CheckoutOfferForm(forms.ModelForm):
    """
    The "produit" itself — the commercial/marketing page shown on the public
    checkout for one course (docs/PRODUCT_CADRAGE_PMI.md §4: "Produit —
    Offre commerciale créée dans notre plateforme et associée à un seul
    cours"). Scoped to per-course offers only; editing the shared fallback
    template (CheckoutOffer.is_default, course=None) still goes through
    Django admin — a rare, special-case edit, not day-to-day produit work.
    `slug` auto-generates from `title` when left blank, same convenience
    Course.save() already gives its own slug.
    """

    class Meta:
        model = CheckoutOffer
        fields = ["course", "title", "subtitle", "language", "slug", "mockup_image", "mockup_alt", "highlights", "closing_note"]
        widgets = {
            "course": forms.Select(attrs={"class": "form-select"}),
            "title": forms.TextInput(attrs={"class": "form-control", "placeholder": "Titre affiché sur la page de paiement"}),
            "subtitle": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "language": forms.Select(attrs={"class": "form-select"}),
            "slug": forms.TextInput(attrs={"class": "form-control", "placeholder": "Généré automatiquement si laissé vide"}),
            "mockup_image": forms.TextInput(attrs={"class": "form-control", "placeholder": "https://... ou images/mon-visuel.jpg"}),
            "mockup_alt": forms.TextInput(attrs={"class": "form-control", "placeholder": "Description de l'image pour l'accessibilité"}),
            "highlights": forms.Textarea(attrs={"class": "form-control", "rows": 4, "placeholder": "Un fait par ligne, ex. : Accès à vie"}),
            "closing_note": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        }
        help_texts = {
            "slug": "Identifiant interne de l'offre — sans effet sur l'URL de paiement, qui utilise le lien du cours.",
            "highlights": "Bénéfices courts affichés au-dessus du récapitulatif, un par ligne.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["course"].queryset = Course.objects.order_by("name")
        self.fields["course"].required = True
        self.fields["slug"].required = False

    def clean_slug(self):
        slug = (self.cleaned_data.get("slug") or "").strip()
        if slug:
            return slug
        base = slugify(self.cleaned_data.get("title", "")) or "offre"
        candidate = base
        suffix = 2
        qs = CheckoutOffer.objects.exclude(pk=self.instance.pk)
        while qs.filter(slug=candidate).exists():
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate


CheckoutBenefitFormSet = inlineformset_factory(
    CheckoutOffer, CheckoutBenefit,
    fields=["title", "description", "is_bonus", "display_order"],
    widgets={
        "title": forms.TextInput(attrs={"class": "form-control form-control-sm", "placeholder": "Titre du bénéfice"}),
        "description": forms.TextInput(attrs={"class": "form-control form-control-sm", "placeholder": "Description courte"}),
        "is_bonus": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        "display_order": forms.NumberInput(attrs={"class": "form-control form-control-sm", "min": 0}),
    },
    extra=3, can_delete=True,
)
