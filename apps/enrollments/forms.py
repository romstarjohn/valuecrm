from django import forms
from apps.courses.models import Course

class EnrollmentForm(forms.Form):
    email = forms.EmailField(
        label="E-mail du client",
        widget=forms.EmailInput(attrs={"class": "form-control", "placeholder": "client@exemple.com"})
    )
    course_id = forms.ChoiceField(
        label="Formation",
        widget=forms.Select(attrs={"class": "form-select"})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["course_id"].choices = [
            (c.cf_course_id, c.name) 
            for c in Course.objects.all().order_by("name")
        ]

class BulkEnrollmentForm(forms.Form):
    emails = forms.CharField(
        label="E-mails des clients",
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "rows": 10,
            "placeholder": "Saisissez un e-mail par ligne..."
        }),
        help_text="Indiquez jusqu'à 50 adresses e-mail."
    )
    course_id = forms.ChoiceField(
        label="Formation",
        widget=forms.Select(attrs={"class": "form-select"})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["course_id"].choices = [
            (c.cf_course_id, c.name) 
            for c in Course.objects.all().order_by("name")
        ]

    def clean_emails(self):
        data = self.cleaned_data["emails"]
        email_list = [e.strip() for e in data.splitlines() if e.strip()]
        if not email_list:
            raise forms.ValidationError("Veuillez fournir au moins une adresse e-mail.")
        if len(email_list) > 50:
            raise forms.ValidationError("50 clients au maximum à la fois — répartissez la liste en plusieurs fois.")
        return email_list
