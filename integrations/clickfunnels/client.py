import time
import requests
from typing import Any, Dict, Optional, List
from shared.constants import DEFAULT_TIMEOUT
from shared.logging_utils import get_logger, log_service_start, log_service_success, log_service_failure
from shared.security import decrypt_value
from .exceptions import (
    ClickFunnelsAPIError,
    ClickFunnelsAuthError,
    ClickFunnelsRateLimitError
)
from .schemas import WorkspaceDTO, TeamDTO, CourseDTO, ContactDTO, EnrollmentDTO

logger = get_logger(__name__)

class ClickFunnelsClient:
    """
    Client for ClickFunnels API v2.
    Uses API Access Token authentication (Private Integration).
    Orchestrates between Account-level and Workspace-level base URLs.
    """
    ACCOUNT_BASE_URL = "https://accounts.myclickfunnels.com/api/v2"

    def __init__(self, api_access_token: str, api_user_agent: str = "ValuedCRM/1.0"):
        self.session = requests.Session()
        self.api_access_token = api_access_token
        self.api_user_agent = api_user_agent

        self.session.headers.update({
            "Authorization": f"Bearer {self.api_access_token}",
            "User-Agent": self.api_user_agent,
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    @classmethod
    def from_configuration(cls, config):
        return cls(
            api_access_token=decrypt_value(config.api_access_token),
            api_user_agent=config.api_user_agent
        )

    def _workspace_base_url(self, subdomain: str) -> str:
        return f"https://{subdomain}.myclickfunnels.com/api/v2"

    def _request(self, method: str, url: str, **kwargs) -> Any:
        method = method.upper()

        # Security: ensure secrets aren't in kwargs passed to logger
        log_service_start(
            logger, "ClickFunnelsClient", "_request",
            endpoint=url, method=method
        )

        start_time = time.time()
        try:
            response = self.session.request(
                method=method,
                url=url,
                timeout=DEFAULT_TIMEOUT,
                **kwargs
            )
            duration_ms = int((time.time() - start_time) * 1000)

            log_context = {
                "endpoint": url,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
                "method": method
            }

            if response.status_code == 401:
                raise ClickFunnelsAuthError("The API access token is invalid or unauthorized.", status_code=401)
            elif response.status_code == 429:
                raise ClickFunnelsRateLimitError("Rate limit exceeded", status_code=429)

            response.raise_for_status()

            # Response could be list or dict
            data = response.json()
            log_service_success(logger, "ClickFunnelsClient", "_request", **log_context)
            return data

        except requests.exceptions.RequestException as e:
            duration_ms = int((time.time() - start_time) * 1000)
            status_code = getattr(e.response, 'status_code', None)
            log_context = {
                "endpoint": url,
                "status_code": status_code,
                "duration_ms": duration_ms,
                "method": method
            }
            log_service_failure(logger, "ClickFunnelsClient", "_request", e, **log_context)

            if status_code:
                raise ClickFunnelsAPIError(str(e), status_code=status_code, response_body=e.response.text)
            raise ClickFunnelsAPIError(str(e))

    # --- Account Level Methods ---

    def validate_credentials(self) -> Dict[str, Any]:
        """Validates token by fetching basic account/user info."""
        url = f"{self.ACCOUNT_BASE_URL}/teams"
        return self._request("GET", url)

    def list_teams(self) -> List[TeamDTO]:
        """Fetches all teams associated with the account."""
        url = f"{self.ACCOUNT_BASE_URL}/teams"
        data = self._request("GET", url)
        # Verified: GET /teams returns a list
        if not isinstance(data, list):
            raise ClickFunnelsAPIError("Teams were returned by ClickFunnels, but the response format could not be parsed.")
        return [TeamDTO.model_validate(item) for item in data]

    def list_workspaces(self, team_id: int) -> List[WorkspaceDTO]:
        """Fetches all workspaces for a specific team."""
        url = f"{self.ACCOUNT_BASE_URL}/teams/{team_id}/workspaces"
        data = self._request("GET", url)
        # Verified: GET /teams/{team_id}/workspaces returns a list
        if not isinstance(data, list):
            raise ClickFunnelsAPIError("Workspaces were returned by ClickFunnels, but the response format could not be parsed.")
        return [WorkspaceDTO.model_validate(item) for item in data]

    # --- Workspace Level Methods ---

    def list_courses(self, subdomain: str, workspace_id: int) -> List[CourseDTO]:
        url = f"{self._workspace_base_url(subdomain)}/workspaces/{workspace_id}/courses"
        data = self._request("GET", url)
        # Many workspace-level endpoints return a list or a paginated object
        # Based on contract: list[dict]
        items = data if isinstance(data, list) else data.get("courses", [])
        return [CourseDTO.model_validate(item) for item in items]

    def get_contact_by_email(self, subdomain: str, workspace_id: int, email: str) -> Optional[ContactDTO]:
        url = f"{self._workspace_base_url(subdomain)}/workspaces/{workspace_id}/contacts"
        # ClickFunnels filters on "filter[email_address]" — an unrecognized
        # "email" param is silently ignored and returns the unfiltered list.
        params = {"filter[email_address]": email}
        data = self._request("GET", url, params=params)
        # Verified: returns list
        if not isinstance(data, list):
            data = data.get("contacts", [])

        return ContactDTO.model_validate(data[0]) if data else None

    def create_contact(self, subdomain: str, workspace_id: int, payload: Dict[str, Any]) -> ContactDTO:
        url = f"{self._workspace_base_url(subdomain)}/workspaces/{workspace_id}/contacts"
        # ClickFunnels requires the attributes nested under "contact", with
        # "email_address" (not "email") as the field name.
        contact_attrs = dict(payload)
        if "email" in contact_attrs:
            contact_attrs["email_address"] = contact_attrs.pop("email")
        data = self._request("POST", url, json={"contact": contact_attrs})
        return ContactDTO.model_validate(data)

    def enroll_contact_in_course(self, subdomain: str, contact_id: int, course_id: int) -> EnrollmentDTO:
        # POST /courses/{course_id}/enrollments — not under /workspaces/{id}/,
        # and the body key is "courses_enrollment" (verified against
        # https://developers.myclickfunnels.com/reference/createcoursesenrollments).
        url = f"{self._workspace_base_url(subdomain)}/courses/{course_id}/enrollments"
        payload = {"courses_enrollment": {"contact_id": contact_id}}
        data = self._request("POST", url, json=payload)
        return EnrollmentDTO.model_validate(data)

    def update_enrollment_suspension(
        self, subdomain: str, enrollment_id: int, suspended: bool, suspension_reason: str = "",
    ) -> EnrollmentDTO:
        # PUT /courses/enrollments/{id} — deliberately NOT
        # /courses/{course_id}/enrollments/{id}; the update endpoint takes
        # only the enrollment's own numeric id, no course_id in the path
        # (verified against https://developers.myclickfunnels.com/reference/updatecoursesenrollments).
        url = f"{self._workspace_base_url(subdomain)}/courses/enrollments/{enrollment_id}"
        payload = {"courses_enrollment": {"suspended": suspended, "suspension_reason": suspension_reason}}
        data = self._request("PUT", url, json=payload)
        return EnrollmentDTO.model_validate(data)
