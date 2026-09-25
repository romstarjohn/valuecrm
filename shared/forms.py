from django.contrib.auth.forms import PasswordChangeForm


class BootstrapPasswordChangeForm(PasswordChangeForm):
    """PasswordChangeForm with .form-control on every field — Django's own
    form doesn't set a widget class, and this project's convention (see
    apps/payments/forms.py, apps/contacts/forms.py) always does."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"
