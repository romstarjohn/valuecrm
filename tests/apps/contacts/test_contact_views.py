import pytest
from django.urls import reverse
from apps.contacts.models import Contact

@pytest.mark.django_db
def test_contact_list_view_authenticated(staff_client):
    Contact.objects.create(email="test@example.com", first_name="John", last_name="Doe")
    url = reverse("contacts:list")
    response = staff_client.get(url)
    assert response.status_code == 200
    assert "John Doe" in response.content.decode()

@pytest.mark.django_db
def test_contact_list_view_unauthenticated(client):
    url = reverse("contacts:list")
    response = client.get(url)
    assert response.status_code == 302  # Redirect to login

@pytest.mark.django_db
def test_contact_detail_view(staff_client):
    contact = Contact.objects.create(email="detail@example.com", first_name="Detail")
    url = reverse("contacts:detail", kwargs={"pk": contact.pk})
    response = staff_client.get(url)
    assert response.status_code == 200
    assert "detail@example.com" in response.content.decode()

@pytest.mark.django_db
def test_contact_create_view(staff_client):
    url = reverse("contacts:add")
    payload = {
        "first_name": "New",
        "last_name": "Contact",
        "email": "new_ui@example.com",
        "status": "active"
    }
    response = staff_client.post(url, data=payload)
    assert response.status_code == 302  # Redirect to detail
    assert Contact.objects.filter(email="new_ui@example.com").exists()

@pytest.mark.django_db
def test_contact_update_view(staff_client):
    contact = Contact.objects.create(email="old@example.com", first_name="Old")
    url = reverse("contacts:edit", kwargs={"pk": contact.pk})
    payload = {
        "first_name": "Updated",
        "last_name": "Name",
        "email": "old@example.com",
        "status": "active"
    }
    response = staff_client.post(url, data=payload)
    assert response.status_code == 302
    contact.refresh_from_db()
    assert contact.first_name == "Updated"
