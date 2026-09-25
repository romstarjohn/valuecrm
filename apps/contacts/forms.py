from django import forms
from .models import Contact


class ContactForm(forms.ModelForm):
    """
    Client form for non-technical staff (docs/UI_VOCABULARY.md): only what a
    person actually knows about a client. Tags are typed as a comma-separated
    list instead of JSON. The address/placeholder JSON fields synced from
    ClickFunnels are deliberately not editable here — excluding them from the
    form leaves their stored values untouched.
    """

    tags = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ex. : VIP, promo rentrée"}),
        help_text="Séparez les étiquettes par des virgules.",
    )

    class Meta:
        model = Contact
        fields = ["first_name", "last_name", "email", "phone", "source", "time_zone", "tags"]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Ex. : Sarah", "autocomplete": "given-name"}),
            "last_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Ex. : Nguimgo", "autocomplete": "family-name"}),
            "email": forms.EmailInput(attrs={"class": "form-control", "placeholder": "vous@exemple.com", "autocomplete": "email"}),
            "phone": forms.TextInput(attrs={"class": "form-control", "placeholder": "Ex. : +237 6 90 00 00 00", "autocomplete": "tel"}),
            "source": forms.TextInput(attrs={"class": "form-control", "placeholder": "Ex. : Instagram, bouche-à-oreille"}),
            "time_zone": forms.TextInput(attrs={"class": "form-control", "placeholder": "Ex. : Africa/Douala"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and not self.is_bound:
            existing = self.instance.tags if isinstance(self.instance.tags, list) else []
            self.initial["tags"] = ", ".join(str(tag) for tag in existing)

    def clean_tags(self):
        raw = self.cleaned_data.get("tags") or ""
        seen = []
        for part in raw.split(","):
            tag = part.strip()
            if tag and tag not in seen:
                seen.append(tag)
        return seen
