from typing import List
from ninja import Router
from django.shortcuts import get_object_or_404
from .models import Contact
from .schemas import ContactSchema, ContactCreateSchema, ContactUpdateSchema
from .services import ContactService

router = Router(tags=["Contacts"])

@router.get("/", response=List[ContactSchema])
def list_contacts(request):
    service = ContactService()
    return service.list_contacts()

@router.post("/", response=ContactSchema)
def create_contact(request, data: ContactCreateSchema):
    service = ContactService()
    return service.create_local_contact(data.dict())

@router.get("/{contact_id}", response=ContactSchema)
def get_contact(request, contact_id: int):
    return get_object_or_404(Contact, id=contact_id)

@router.patch("/{contact_id}", response=ContactSchema)
def update_contact(request, contact_id: int, data: ContactUpdateSchema):
    contact = get_object_or_404(Contact, id=contact_id)
    service = ContactService()
    return service.update_local_contact(contact, data.dict(exclude_unset=True))
