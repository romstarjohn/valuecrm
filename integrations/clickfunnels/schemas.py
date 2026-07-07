from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict

class BaseCFDTO(BaseModel):
    model_config = ConfigDict(extra='allow')

class TeamDTO(BaseCFDTO):
    id: int | str
    public_id: str
    name: str

class WorkspaceDTO(BaseCFDTO):
    id: int | str
    public_id: str
    team_id: int | str
    name: str
    subdomain: Optional[str] = None

class CourseDTO(BaseCFDTO):
    id: int | str
    public_id: Optional[str] = None
    title: str
    published_at: Optional[datetime] = None
    root_section_id: Optional[int | str] = None
    description: str
    current_path: Optional[str] = None
    sharing_fingerprint: Optional[str] = None
    show_in_community: Optional[bool] = None
    show_to_non_members: Optional[bool] = None
    upgrade_url: Optional[str] = None
    redirect_to_full_course: Optional[bool] = None
    unauthorized_redirect_url: Optional[str] = None
    comments_enabled: Optional[bool] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    image_url: Optional[str] = None

class ContactDTO(BaseCFDTO):
    id: int | str
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None

class EnrollmentDTO(BaseCFDTO):
    id: int | str
    contact_id: int | str
    course_id: int | str
    status: str
