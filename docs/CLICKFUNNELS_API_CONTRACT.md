# ClickFunnels API Contract (Verified)

This document defines the official response shapes for the ClickFunnels API v2, based on documented behavior and verified sample captures.

## Common Rules
- **Extra Fields:** DTOs must allow extra fields (`model_config = ConfigDict(extra='allow')`) to ensure forward compatibility.
- **ID Types:** Primary internal IDs are **integers** or **strings**.
- **Flexible ID Types:** Real API responses may return numeric IDs for fields ending in `_id` (e.g., `root_section_id`) even when documentation suggests they are strings. DTOs should support `int | str` for these fields.
- **Timestamps:** ISO-8601 strings, parsed as `datetime` objects.

---

## Teams
**GET** `https://accounts.myclickfunnels.com/api/v2/teams`

**Response (List of Objects):**
```json
[
  {
    "id": 193460,
    "public_id": "JNzNaa",
    "name": "Richard Steinmetz's Team"
  }
]
```

---

## Workspaces
**GET** `https://accounts.myclickfunnels.com/api/v2/teams/{team_id}/workspaces`

**Response (List of Objects):**
```json
[
  {
    "id": 198218,
    "public_id": "example_public_id",
    "team_id": 193460,
    "name": "Hammer Workspace",
    "subdomain": "hammer"
  }
]
```

---

## Courses
**GET** `https://{subdomain}.myclickfunnels.com/api/v2/workspaces/{workspace_id}/courses`

**Verified Object Contract:**
- `id`: integer (Required)
- `public_id`: string | null
- `title`: string (Required)
- `published_at`: datetime | null
- `root_section_id`: string | null
- `description`: string (Required)
- `current_path`: string | null
- `sharing_fingerprint`: string | null
- `show_in_community`: boolean | null
- `show_to_non_members`: boolean | null
- `upgrade_url`: string | null
- `redirect_to_full_course`: boolean | null
- `unauthorized_redirect_url`: string | null
- `comments_enabled`: boolean | null
- `created_at`: datetime | null
- `updated_at`: datetime | null
- `image_url`: string | null

**Sample JSON:**
```json
{
  "id": 331,
  "public_id": null,
  "title": "My Course",
  "published_at": null,
  "root_section_id": null,
  "description": "Course description",
  "current_path": "/courses/my-course",
  "sharing_fingerprint": null,
  "show_in_community": true,
  "show_to_non_members": false,
  "upgrade_url": null,
  "redirect_to_full_course": null,
  "unauthorized_redirect_url": null,
  "comments_enabled": true,
  "created_at": null,
  "updated_at": null,
  "image_url": "https://example.com/course.png"
}
```

---

## Contacts
**GET** `https://{subdomain}.myclickfunnels.com/api/v2/workspaces/{workspace_id}/contacts`

**Response (List of Objects):**
```json
[
  {
    "id": 33,
    "email": "example@example.com"
  }
]
```
