# API Documentation

ValuedCRM provides a RESTful API powered by **Django Ninja**. 

## Authentication
All endpoints (except `/health`) require **Django Session Authentication**. You must be logged into the Django Admin in your current browser session to access the API or the Swagger documentation.

Interactive documentation is available at `/api/docs` (only when `DEBUG=True`).

## System
- **GET** `/api/health`: Returns application health status (`{"status": "ok"}`).

## Configuration
- **GET** `/api/config/active`: Retrieve the currently active ClickFunnels configuration.
- **POST** `/api/config/{id}/verify`: Manually trigger credential verification for a configuration.

## Contacts
- **GET** `/api/contacts/`: List all local contacts.
- **POST** `/api/contacts/`: Create a new local contact.
- **GET** `/api/contacts/{id}`: Retrieve detailed information for a specific contact.
- **PATCH** `/api/contacts/{id}`: Partially update a contact's information.

## Courses
- **GET** `/api/courses/`: List all local courses.
- **GET** `/api/courses/{cf_course_id}`: Retrieve detailed information for a specific course.
- **POST** `/api/courses/sync`: Trigger a course sync from ClickFunnels.
    - **Body:** `{"workspace_id": "string"}`

## Enrollments
- **POST** `/api/enrollments/`: Enroll a single contact into a course.
    - **Body:** `{"email": "string", "course_id": "string"}`
- **POST** `/api/enrollments/bulk`: Enroll multiple contacts into a course (Limit: 50).
    - **Body:** `{"emails": ["string"], "course_id": "string"}`

### Enrollment Statuses
| Status | Description |
| :--- | :--- |
| `SUCCESS` | Successfully enrolled in ClickFunnels and recorded locally. |
| `FAILURE` | Enrollment failed (either contact sync error or CF API error). |

## Error Responses
- `401 Unauthorized`: No active staff session.
- `400 Bad Request`: Invalid payload or business logic violation.
- `404 Not Found`: Resource or active configuration not found.
- `500 Internal Server Error`: Unexpected system failure.
