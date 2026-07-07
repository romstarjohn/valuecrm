from django import forms
from .models import Contact

class ContactForm(forms.ModelForm):
    class Meta:
        model = Contact
        fields = [
            "first_name", "last_name", "email", "phone",
            "status", "source", "time_zone", "tags",
            "shipping_address", "billing_address",
            "payment_method_placeholder", "order_information_placeholder"
        ]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "First Name"}),
            "last_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Last Name"}),
            "email": forms.EmailInput(attrs={"class": "form-control", "placeholder": "Email Address"}),
            "phone": forms.TextInput(attrs={"class": "form-control", "placeholder": "Phone Number"}),
            "status": forms.TextInput(attrs={"class": "form-control", "placeholder": "Status"}),
            "source": forms.TextInput(attrs={"class": "form-control", "placeholder": "Source"}),
            "time_zone": forms.TextInput(attrs={"class": "form-control", "placeholder": "Time Zone"}),
            "tags": forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "JSON tags list"}),
            "shipping_address": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "JSON shipping data"}),
            "billing_address": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "JSON billing data"}),
            "payment_method_placeholder": forms.TextInput(attrs={"class": "form-control"}),
            "order_information_placeholder": forms.TextInput(attrs={"class": "form-control"}),
        }
