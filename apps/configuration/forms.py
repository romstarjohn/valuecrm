from django import forms
from .models import ClickFunnelsConfig

class ClickFunnelsSettingsForm(forms.ModelForm):
    """
    Step 1 Form: Token and Basic Info
    """
    api_access_token = forms.CharField(
        widget=forms.PasswordInput(render_value=False), 
        required=False,
        help_text="Leave blank to keep current token.",
        label="API Access Token"
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

class TeamSelectionForm(forms.Form):
    """
    Step 2 Form: Team selection
    """
    team_id = forms.ChoiceField(
        label="Select Team",
        widget=forms.Select(attrs={"class": "form-select"})
    )

class WorkspaceSelectionForm(forms.Form):
    """
    Step 3 Form: Workspace selection
    """
    workspace_id = forms.ChoiceField(
        label="Select Workspace",
        widget=forms.Select(attrs={"class": "form-select"})
    )
