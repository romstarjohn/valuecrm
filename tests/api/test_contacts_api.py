import pytest
from django.test import Client
from apps.contacts.models import Contact

@pytest.mark.django_db
def test_list_contacts_api(staff_client):
    Contact.objects.create(email="c1@example.com")
    Contact.objects.create(email="c2@example.com")
    
    response = staff_client.get("/api/contacts/")
    assert response.status_code == 200
    assert len(response.json()) == 2

@pytest.mark.django_db
def test_create_contact_api(staff_client):
    payload = {"email": "new@example.com", "first_name": "New"}
    response = staff_client.post("/api/contacts/", data=payload, content_type="application/json")
    assert response.status_code == 200
    assert response.json()["email"] == "new@example.com"
    assert Contact.objects.filter(email="new@example.com").exists()

@pytest.mark.django_db
def test_update_contact_api(staff_client):
    contact = Contact.objects.create(email="old@example.com", first_name="Old")
    payload = {"first_name": "Updated"}
    response = staff_client.patch(f"/api/contacts/{contact.id}", data=payload, content_type="application/json")
    assert response.status_code == 200
    assert response.json()["first_name"] == "Updated"
    contact.refresh_from_db()
    assert contact.first_name == "Updated"
