from django import forms

class CourseSyncForm(forms.Form):
    """
    Simple form to trigger course sync for a specific workspace.
    """
    workspace_id = forms.CharField(
        label="Workspace ID",
        max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Enter Workspace ID to sync"})
    )
