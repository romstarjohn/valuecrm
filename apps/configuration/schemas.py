from typing import Optional
from ninja import Schema

class CredentialsSchema(Schema):
    api_access_token: str
    api_user_agent: str = "ValuedCRM/1.0"

class ConfigResponseSchema(Schema):
    id: int
    name: str
    is_active: bool
    validation_status: str
    workspace_id: Optional[str] = None
    workspace_name: Optional[str] = None
    team_id: Optional[str] = None
    team_name: Optional[str] = None
    api_user_agent: str
    business_info: dict
