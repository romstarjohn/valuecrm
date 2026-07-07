from django import forms
from apps.courses.models import Course

class EnrollmentForm(forms.Form):
    email = forms.EmailField(
        label="Student Email",
        widget=forms.EmailInput(attrs={"class": "form-control", "placeholder": "student@example.com"})
    )
    course_id = forms.ChoiceField(
        label="Select Course",
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
        label="Student Emails",
        widget=forms.Textarea(attrs={
            "class": "form-control", 
            "rows": 10, 
            "placeholder": "Enter one email per line..."
        }),
        help_text="Provide up to 50 email addresses."
    )
    course_id = forms.ChoiceField(
        label="Select Course",
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
            raise forms.ValidationError("Please provide at least one email address.")
        if len(email_list) > 50:
            raise forms.ValidationError("Maximum 50 emails allowed per bulk request.")
        return email_list
