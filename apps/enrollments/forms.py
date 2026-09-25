from django import forms
from apps.courses.models import Course

class EnrollmentForm(forms.Form):
    email = forms.EmailField(
        label="E-mail de l'étudiant",
        widget=forms.EmailInput(attrs={"class": "form-control", "placeholder": "etudiant@exemple.com"})
    )
    course_id = forms.ChoiceField(
        label="Choisir une formation",
        widget=forms.Select(attrs={"class": "form-select"})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["course_id"].choices = [
            (c.cf_course_id, f"{c.name} ({c.cf_course_id})") 
            for c in Course.objects.all().order_by("name")
        ]

class BulkEnrollmentForm(forms.Form):
    emails = forms.CharField(
        label="E-mails des étudiants",
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "rows": 10,
            "placeholder": "Saisissez un e-mail par ligne..."
        }),
        help_text="Indiquez jusqu'à 50 adresses e-mail."
    )
    course_id = forms.ChoiceField(
        label="Choisir une formation",
        widget=forms.Select(attrs={"class": "form-select"})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["course_id"].choices = [
            (c.cf_course_id, f"{c.name} ({c.cf_course_id})") 
            for c in Course.objects.all().order_by("name")
        ]

    def clean_emails(self):
        data = self.cleaned_data["emails"]
        email_list = [e.strip() for e in data.splitlines() if e.strip()]
        if not email_list:
            raise forms.ValidationError("Veuillez fournir au moins une adresse e-mail.")
        if len(email_list) > 50:
            raise forms.ValidationError("Maximum 50 e-mails autorisés par requête groupée.")
        return email_list
