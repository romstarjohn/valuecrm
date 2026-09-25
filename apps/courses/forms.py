from django import forms
from django.forms import inlineformset_factory
from django.utils.text import slugify

from .models import CheckoutBenefit, CheckoutOffer, Course


MAX_OFFER_IMAGE_BYTES = 3 * 1024 * 1024
_ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}


class CheckoutOfferForm(forms.ModelForm):
    """
    The "page de vente" of one course: what the customer sees on the payment
    link (docs/UI_VOCABULARY.md). Written for non-technical staff — the image
    is uploaded, never typed as a URL; the internal slug is always generated;
    the alt text falls back to the title (CheckoutOffer.save()).

    Pass `course=` when the course is already known (opened from an Offre
    page, or editing): the course is then fixed and not shown as a choice.
    Editing the shared fallback template (is_default, course=None) still goes
    through Django admin.
    """

    class Meta:
        model = CheckoutOffer
        fields = ["course", "title", "subtitle", "language", "image", "mockup_alt", "highlights", "closing_note"]
        labels = {
            "course": "Formation",
            "title": "Titre de la page",
            "subtitle": "Phrase d'accroche",
            "language": "Langue de la page",
            "image": "Image de la formation",
            "mockup_alt": "Description de l'image (pour les personnes malvoyantes)",
            "highlights": "Points forts (un par ligne)",
            "closing_note": "Message de fin (facultatif)",
        }
        widgets = {
            "course": forms.Select(attrs={"class": "form-select"}),
            "title": forms.TextInput(attrs={"class": "form-control", "placeholder": "Ex. : Cheveux crépus longs et libres"}),
            "subtitle": forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Ex. : La méthode complète pour faire pousser vos cheveux en milieu tropical."}),
            "language": forms.Select(attrs={"class": "form-select"}),
            "image": forms.ClearableFileInput(attrs={"class": "form-control", "accept": "image/jpeg,image/png,image/webp"}),
            "mockup_alt": forms.TextInput(attrs={"class": "form-control", "placeholder": "Laissez vide pour utiliser le titre"}),
            "highlights": forms.Textarea(attrs={"class": "form-control", "rows": 4, "placeholder": "Accès à vie\nAucune connaissance préalable requise\nÀ votre rythme"}),
            "closing_note": forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Ex. : Une question ? Écrivez-nous sur WhatsApp."}),
        }
        help_texts = {
            "image": "JPG, PNG ou WEBP, 3 Mo maximum. Format paysage conseillé (par ex. 1536 × 1024).",
            "highlights": "Affichés en liste à côté de l'image. Une idée courte par ligne.",
        }

    def __init__(self, *args, course=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fixed_course = course or (self.instance.course if self.instance.pk else None)
        if self.fixed_course is not None:
            del self.fields["course"]
            self.instance.course = self.fixed_course
        else:
            # Only courses without their own page yet — one page per course.
            self.fields["course"].queryset = Course.objects.filter(checkout_offer__isnull=True).order_by("name")
            self.fields["course"].required = True
        self.fields["language"].choices = [("fr", "Français"), ("en", "Anglais")]
        if not self.instance.pk:
            self.initial.setdefault("language", "fr")
        self.fields["mockup_alt"].required = False

    def clean_image(self):
        image = self.cleaned_data.get("image")
        # Unchanged or cleared: nothing new to validate.
        if not image or not hasattr(image, "content_type"):
            return image
        if image.size > MAX_OFFER_IMAGE_BYTES:
            raise forms.ValidationError("L'image est trop lourde (3 Mo maximum). Réduisez-la puis réessayez.")
        detected = getattr(getattr(image, "image", None), "format", None)
        if detected not in _ALLOWED_IMAGE_FORMATS:
            raise forms.ValidationError("Format non accepté. Utilisez une image JPG, PNG ou WEBP.")
        return image

    def clean(self):
        cleaned = super().clean()
        has_image = cleaned.get("image") or (self.instance.pk and (self.instance.image or self.instance.mockup_image))
        if cleaned.get("image") is False:  # "effacer" ticked
            has_image = bool(self.instance.mockup_image)
        if not has_image:
            self.add_error("image", "Ajoutez une image : elle est affichée en grand sur la page de paiement.")
        return cleaned

    def save(self, commit=True):
        offer = super().save(commit=False)
        if not offer.slug:
            base = slugify(offer.title) or "offre"
            candidate, suffix = base, 2
            while CheckoutOffer.objects.exclude(pk=offer.pk).filter(slug=candidate).exists():
                candidate = f"{base}-{suffix}"
                suffix += 1
            offer.slug = candidate
        if commit:
            offer.save()
        return offer


class _BenefitForm(forms.ModelForm):
    class Meta:
        model = CheckoutBenefit
        fields = ["title", "description", "is_bonus"]
        labels = {
            "title": "Titre",
            "description": "Description",
            "is_bonus": "Mettre en avant comme bonus",
        }
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control", "placeholder": "Ex. : 10 heures de vidéo"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Ex. : Des leçons pas à pas, à regarder quand vous voulez."}),
            "is_bonus": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


# Row order = display order: the page keeps the rows in the order the user
# sees (hidden ORDER field, set by the up/down buttons) and the view writes
# display_order from it — no number to type.
CheckoutBenefitFormSet = inlineformset_factory(
    CheckoutOffer, CheckoutBenefit, form=_BenefitForm,
    extra=2, can_delete=True, can_order=True,
)


def save_benefits_in_row_order(formset, offer):
    """Saves the formset with display_order = position among the kept rows (0, 1, 2…)."""
    formset.instance = offer
    formset.save(commit=False)
    for obj in formset.deleted_objects:
        obj.delete()
    for position, form in enumerate(formset.ordered_forms):
        benefit = form.save(commit=False)
        benefit.offer = offer
        benefit.display_order = position
        benefit.save()
