from typing import List, Optional
from ninja import Schema

class CourseSchema(Schema):
    cf_course_id: str
    workspace_id: str
    name: str
    description: str

class SyncCoursesRequestSchema(Schema):
    workspace_id: str

class SyncCoursesResponseSchema(Schema):
    synced_count: int
    created_count: int
    updated_count: int
