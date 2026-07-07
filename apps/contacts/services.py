from typing import List, Optional, Dict, Any
from shared.logging_utils import get_logger, log_service_start, log_service_success, log_service_failure
from .models import Contact
from integrations.clickfunnels.schemas import ContactDTO

logger = get_logger(__name__)

class ContactService:
    def __init__(self, client=None):
        self.client = client

    def list_contacts(self) -> List[Contact]:
        return Contact.objects.all().order_by("-created_at")

    def get_by_email(self, email: str) -> Optional[Contact]:
        return Contact.objects.filter(email=email).first()

    def create_local_contact(self, data: Dict[str, Any]) -> Contact:
        log_service_start(logger, "ContactService", "create_local_contact", email=data.get("email"))
        try:
            contact = Contact.objects.create(**data)
            log_service_success(logger, "ContactService", "create_local_contact", contact_id=contact.id)
            return contact
        except Exception as e:
            log_service_failure(logger, "ContactService", "create_local_contact", e, email=data.get("email"))
            raise e

    def update_local_contact(self, contact: Contact, data: Dict[str, Any]) -> Contact:
        log_service_start(logger, "ContactService", "update_local_contact", contact_id=contact.id)
        try:
            for attr, value in data.items():
                if value is not None:
                    setattr(contact, attr, value)
            contact.save()
            log_service_success(logger, "ContactService", "update_local_contact", contact_id=contact.id)
            return contact
        except Exception as e:
            log_service_failure(logger, "ContactService", "update_local_contact", e, contact_id=contact.id)
            raise e

    def get_or_create_remote_contact(self, subdomain: str, workspace_id: int, contact_data: Dict[str, Any]) -> Contact:
        """
        Ensures a contact exists both locally and in ClickFunnels.
        Aligns with v2 contract: requires subdomain and integer workspace_id.
        """
        email = contact_data.get("email")
        log_service_start(logger, "ContactService", "get_or_create_remote_contact", email=email, workspace_id=workspace_id)
        
        try:
            # 1. Check Local
            local_contact = self.get_by_email(email)
            
            # 2. Check Remote via Client
            remote_contact_dto = self.client.get_contact_by_email(subdomain, workspace_id, email)
            
            if not remote_contact_dto:
                # 3. Create Remote if missing
                remote_contact_dto = self.client.create_contact(subdomain, workspace_id, contact_data)
            
            # 4. Sync Local
            if not local_contact:
                local_contact = Contact(email=email)
            
            local_contact.cf_contact_id = str(remote_contact_dto.id)
            local_contact.first_name = remote_contact_dto.first_name or local_contact.first_name
            local_contact.last_name = remote_contact_dto.last_name or local_contact.last_name
            local_contact.save()
            
            log_service_success(logger, "ContactService", "get_or_create_remote_contact", contact_id=local_contact.id)
            return local_contact
            
        except Exception as e:
            log_service_failure(logger, "ContactService", "get_or_create_remote_contact", e, email=email)
            raise e
