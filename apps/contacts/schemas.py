from typing import List, Optional
class Schema: pass # Placeholder until I confirm ninja import works or just use ninja.Schema
from ninja import Schema

class ContactSchema(Schema):
    id: int
    first_name: str
    last_name: str
    email: str
    phone: str
    status: str
    source: str
    cf_contact_id: Optional[str] = None

class ContactCreateSchema(Schema):
    email: str
    first_name: Optional[str] = ""
    last_name: Optional[str] = ""
    phone: Optional[str] = ""

class ContactUpdateSchema(Schema):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    status: Optional[str] = None
