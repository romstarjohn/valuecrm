from django import forms
from .models import ClickFunnelsConfig
from apps.payments.models import TaraConfig

class ClickFunnelsSettingsForm(forms.ModelForm):
    """
    Step 1 Form: Token and Basic Info
    """
    api_access_token = forms.CharField(
        widget=forms.PasswordInput(render_value=False), 
        required=False,
        help_text="Laissez vide pour conserver le jeton actuel.",
        label="Jeton d’accès API"
    )

    class Meta:
        model = ClickFunnelsConfig
        fields = ["name", "is_active", "api_user_agent"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "api_user_agent": forms.TextInput(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in self.fields:
            if field_name != "is_active":
                self.fields[field_name].widget.attrs.update({"class": "form-control"})

class TaraConfigSettingsForm(forms.ModelForm):
    """
    Tara sibling of ClickFunnelsSettingsForm above — same write-only-secret
    structure: api_key/webhook_secret are declared here but deliberately kept
    OUT of Meta.fields, so Django's ModelForm construct_instance() never
    touches those model fields at all; the view pops them from cleaned_data
    and hands them to TaraConfigService.update_credentials() instead (mirrors
    apps/configuration/views.py::settings_view's "save_token" branch exactly,
    and the identical write-only-field fix already applied to
    apps/payments/admin.py::TaraConfigForm / apps/configuration/admin.py::
    ClickFunnelsConfigForm — see those docstrings for the underlying
    Meta.fields-vs-Meta.exclude ModelAdmin gotcha this same pattern avoids
    here too, defensively, even though this is a plain view form).
    """
    api_key = forms.CharField(
        widget=forms.PasswordInput(render_value=False),
        required=False,
        help_text="Laissez vide pour conserver la clé actuelle.",
        label="Clé API",
    )
    webhook_secret = forms.CharField(
        widget=forms.PasswordInput(render_value=False),
        required=False,
        help_text="Laissez vide pour conserver le secret actuel.",
        label="Secret du webhook",
    )

    class Meta:
        model = TaraConfig
        fields = ["name", "business_id", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "business_id": forms.TextInput(attrs={"class": "form-control"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in self.fields:
            if field_name != "is_active":
                self.fields[field_name].widget.attrs.update({"class": "form-control"})

    def clean_business_id(self):
        business_id = (self.cleaned_data.get("business_id") or "").strip()
        if not business_id:
            raise forms.ValidationError("L’ID d’entreprise est obligatoire.")
        return business_id

    def clean(self):
        """
        First-time configuration (no stored ciphertext yet) requires both
        secrets; an existing configuration may leave either blank to keep its
        current value — never erased by a blank submission (see this form's
        docstring / the view, which never calls update_credentials with an
        empty value for either field).
        """
        cleaned_data = super().clean()
        has_existing_api_key = bool(self.instance.pk and self.instance.api_key)
        has_existing_webhook_secret = bool(self.instance.pk and self.instance.webhook_secret)

        if not cleaned_data.get("api_key") and not has_existing_api_key:
            self.add_error("api_key", "La clé API est obligatoire.")
        if not cleaned_data.get("webhook_secret") and not has_existing_webhook_secret:
            self.add_error("webhook_secret", "Le secret du webhook est obligatoire.")
        return cleaned_data


class TeamSelectionForm(forms.Form):
    """
    Step 2 Form: Team selection
    """
    team_id = forms.ChoiceField(
        label="Sélectionner l’équipe",
        widget=forms.Select(attrs={"class": "form-select"})
    )

class WorkspaceSelectionForm(forms.Form):
    """
    Step 3 Form: Workspace selection
    """
    workspace_id = forms.ChoiceField(
        label="Sélectionner l’espace de travail",
        widget=forms.Select(attrs={"class": "form-select"})
    )
