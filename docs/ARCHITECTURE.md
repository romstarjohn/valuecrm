# Architecture Documentation - ValuedCRM MVP

## Overview
ValuedCRM is a focused Django application designed to bridge external contact management with ClickFunnels v2 course enrollments. It prioritizes the Django Admin for all operational tasks and uses Django Ninja for API-driven interactions.

## Project File Tree
```text
valuedcrm/
├── .gitignore                  # Excludes .venv, .env, __pycache__, etc.
├── requirements.txt            # Project dependencies
├── manage.py
├── pytest.ini                  # Pytest configuration
├── core/
│   ├── settings.py             # PostgreSQL & structured LOGGING
│   ├── urls.py                 # Ninja API registration
│   ├── wsgi.py
│   └── asgi.py
├── apps/
│   ├── configuration/
│   │   ├── admin.py
│   │   ├── api.py
│   │   ├── models.py
│   │   ├── schemas.py
│   │   └── services.py
│   ├── contacts/
│   │   ├── admin.py
│   │   ├── api.py
│   │   ├── models.py
│   │   ├── schemas.py
│   │   └── services.py
│   ├── courses/
│   │   ├── admin.py
│   │   ├── api.py
│   │   ├── models.py
│   │   ├── schemas.py
│   │   └── services.py
│   └── enrollments/
│       ├── admin.py
│       ├── api.py
│       ├── models.py
│       ├── schemas.py
│       └── services.py
├── integrations/
│   └── clickfunnels/
│       ├── client.py           # Main CFv2 API Client
│       ├── exceptions.py       # CF Specific exceptions
│       └── schemas.py          # CF API Request/Response schemas
├── shared/
│   ├── constants.py            # Global constants (Limits, etc.)
│   ├── logging_utils.py        # Structured logging helpers
│   ├── models.py               # TimeStampedModel base
│   └── security.py             # Fernet encryption utilities
├── tests/
│   ├── conftest.py
│   ├── apps/
│   ├── integrations/
│   └── shared/
└── docs/
    └── ARCHITECTURE.md         # This file
```

## System Components

### 1. Configuration (`apps.configuration`)
* **Model:** `ClickFunnelsConfig` (Stores credentials and `business_info` JSON).
* **Service:** `ConfigurationService` handles credential validation and selection.

### 2. Contacts (`apps.contacts`)
* **Model:** `Contact` (Local cache of CFv2 identity).
* **Service:** `ContactService` ensures local/remote consistency.

### 3. Courses (`apps.courses`)
* **Model:** `Course` (Local catalog of CFv2 products).
* **Service:** `CourseService` manages manual syncing from CFv2.

### 4. Enrollments (`apps.enrollments`)
* **Model:** `EnrollmentAttempt` (Records of success/failure).
* **Service:** `EnrollmentService` orchestrates the synchronous enrollment flow.

## Admin Features
* **ClickFunnelsConfigAdmin:** Masked secrets in UI; "Verify Credentials" custom action.
* **CourseAdmin:** "Sync Courses" custom action to refresh local catalog.
* **ContactAdmin:** Standard CRUD for contact management.
* **EnrollmentAttemptAdmin:** Read-only audit log of all enrollment attempts.

## Service & Integration Layer
All logic is encapsulated in services that adhere to:
1. **Logging:** Every method must log start, success, and failure (with exceptions).
2. **Dependency Injection:** External dependencies (like `ClickFunnelsClient`) are passed via constructor.
3. **Isolation:** `ClickFunnelsClient` is the sole module allowed to make HTTP requests to CFv2.
4. **Auth:** Authentication implementation must be verified against ClickFunnels API v2 docs before coding. Auth logic must remain isolated inside `ClickFunnelsClient`.

## Security Architecture
* **Encryption:** Symmetric Fernet encryption using `FIELD_ENCRYPTION_KEY` from `.env` (local config only).
* **Shared Security:** Logic resides in `shared/security.py`.
* **Admin Masking:** Sensitive fields are masked using custom Admin widgets.
* **Logging Safety:** Never log secrets, tokens, auth headers, full contact payloads, full payment payloads, or full order payloads. Safe IDs like `workspace_id`, `contact_id`, `course_id`, and `enrollment_attempt_id` may be logged.

## Testing Strategy
* **Service Tests:** Unit tests for all 4 services.
* **Client Tests:** Integration tests for `ClickFunnelsClient` with full HTTP mocking.
* **API Tests:** End-to-end Ninja router tests.
* **Security Tests:** Verify that `logger` calls do not contain decrypted secrets or sensitive payloads.

## Step 1 Readiness Checklist
The following files/structure must be initialized before feature implementation:
- `.gitignore`
- `requirements.txt`
- `manage.py`
- `core/settings.py`
- `core/urls.py`
- `core/wsgi.py`
- `pytest.ini`
- `tests/conftest.py`
- `shared/models.py`
- `shared/constants.py`
- `shared/logging_utils.py`
- `shared/security.py`
