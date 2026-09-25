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
            "first_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Prénom"}),
            "last_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Nom"}),
            "email": forms.EmailInput(attrs={"class": "form-control", "placeholder": "Adresse e-mail"}),
            "phone": forms.TextInput(attrs={"class": "form-control", "placeholder": "Numéro de téléphone"}),
            "status": forms.TextInput(attrs={"class": "form-control", "placeholder": "Statut"}),
            "source": forms.TextInput(attrs={"class": "form-control", "placeholder": "Source"}),
            "time_zone": forms.TextInput(attrs={"class": "form-control", "placeholder": "Fuseau horaire"}),
            "tags": forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Liste de tags JSON"}),
            "shipping_address": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Données de livraison JSON"}),
            "billing_address": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Données de facturation JSON"}),
            "payment_method_placeholder": forms.TextInput(attrs={"class": "form-control"}),
            "order_information_placeholder": forms.TextInput(attrs={"class": "form-control"}),
        }
