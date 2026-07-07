import pytest
from unittest.mock import MagicMock
from apps.contacts.models import Contact
from apps.contacts.services import ContactService
from integrations.clickfunnels.schemas import ContactDTO

@pytest.mark.django_db
def test_create_local_contact():
    service = ContactService()
    data = {"email": "test@example.com", "first_name": "John"}
    contact = service.create_local_contact(data)
    assert contact.email == "test@example.com"
    assert contact.first_name == "John"
    assert Contact.objects.count() == 1

@pytest.mark.django_db
def test_get_or_create_remote_contact_new_local(mocker):
    mock_client = MagicMock()
    # Mock remote contact exists with integer ID
    mock_client.get_contact_by_email.return_value = ContactDTO(
        id=33, 
        email="remote@example.com", 
        first_name="Remote", 
        last_name="User"
    )
    
    service = ContactService(client=mock_client)
    # Refactored signature: (subdomain, workspace_id, contact_data)
    contact = service.get_or_create_remote_contact("hammer", 198218, {"email": "remote@example.com"})
    
    assert contact.email == "remote@example.com"
    assert contact.cf_contact_id == "33"
    assert contact.first_name == "Remote"
    assert Contact.objects.count() == 1
    mock_client.get_contact_by_email.assert_called_once_with("hammer", 198218, "remote@example.com")

@pytest.mark.django_db
def test_get_or_create_remote_contact_missing_remote(mocker):
    mock_client = MagicMock()
    # Remote does not exist initially
    mock_client.get_contact_by_email.return_value = None
    # Mock creation with integer ID
    mock_client.create_contact.return_value = ContactDTO(
        id=44, 
        email="new@example.com"
    )
    
    service = ContactService(client=mock_client)
    contact = service.get_or_create_remote_contact("hammer", 198218, {"email": "new@example.com"})
    
    assert contact.cf_contact_id == "44"
    mock_client.create_contact.assert_called_once()

@pytest.mark.django_db
def test_contact_payload_not_logged(caplog):
    import logging
    logger = logging.getLogger("apps.contacts.services")
    service = ContactService()
    
    payload = {"email": "sensitive@example.com", "first_name": "Secret", "phone": "123456"}
    service.create_local_contact(payload)
    
    log_text = caplog.text
    assert "sensitive@example.com" not in log_text
    assert "Secret" not in log_text
