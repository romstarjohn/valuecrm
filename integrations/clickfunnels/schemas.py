from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict

class BaseCFDTO(BaseModel):
    model_config = ConfigDict(extra='allow')

class TeamDTO(BaseCFDTO):
    id: int | str
    public_id: Optional[str] = None
    name: str

class WorkspaceDTO(BaseCFDTO):
    id: int | str
    public_id: Optional[str] = None
    team_id: int | str
    name: str
    subdomain: str

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
    # ClickFunnels' real field is "email_address", not "email", and it's
    # nullable — anonymous/phone-only contacts have no email on file.
    email_address: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None

class EnrollmentDTO(BaseCFDTO):
    id: int | str
    contact_id: int | str
    course_id: int | str
    # No "status" field exists in ClickFunnels' create-enrollment response —
    # success is indicated purely by the 201 HTTP status. The update/PUT
    # response (Courses::Enrollment#update) echoes these back.
    suspended: Optional[bool] = None
    suspension_reason: Optional[str] = None
