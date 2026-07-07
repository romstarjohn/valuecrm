# GEMINI.md

## Project Overview

This project is a Django-based admin application used to manage ClickFunnels contacts, courses, and enrollments.

The goal is simplicity, maintainability, and clean architecture.

This is an MVP.

Do not introduce enterprise patterns unless explicitly requested.

---

## Technology Stack

* **Python:** 3.13+
* **Framework:** Django 5.x (using `django.contrib.auth` for authentication)
* **API:** Django Ninja
* **Database:** PostgreSQL (PostgreSQL-ready from Step 1)
* **Testing:** Pytest / Pytest-Django
* **Integration:** ClickFunnels API v2
* **Security:** Fernet (cryptography) for secret encryption

---

## Technical Constraints & Decisions

### UI & UX
* **Primary UI:** Django Admin ONLY.
* **Customization:** Django Template Language (DTL) for admin overrides only.
* **No Frontend Frameworks:** No React, Vue, HTMX, Tailwind, or Bootstrap unless required for minimal admin overrides.
* **Forms:** Django ModelForms for Admin; Ninja Schemas for API.

### Infrastructure & Operations
* **Background Jobs:** NONE. All processes (including bulk enrollment) run synchronously.
* **Bulk Limits:** `MAX_BULK_ENROLLMENT_SIZE = 50`.
* **Logging:** Log to `stdout` using Django LOGGING config. 
* **Secret Management:** Use `FIELD_ENCRYPTION_KEY` from `.env` (local config, excluded by `.gitignore`). Use Fernet for encryption. Keep encryption logic in utilities/services, not models.
* **Multi-tenancy:** Single account instance. Support one active ClickFunnels configuration.

---

## Development Philosophy

Prioritize:

1. Simplicity
2. Readability
3. Testability
4. Explicit code

Avoid:

* Over-engineering
* Premature optimization
* Generic frameworks
* Deep inheritance hierarchies
* Complex design patterns

If a simple service class solves the problem, use it.

---

## Naming Conventions

### Variables
Use snake_case (e.g., `user_email`).

### Functions
Use snake_case (e.g., `create_contact()`).

### Classes
Use CamelCase (e.g., `ContactService`).

### Constants
Use UPPER_CASE (e.g., `MAX_BATCH_SIZE`).

---

## Application Structure

```text
apps/
  configuration/
  contacts/
  courses/
  enrollments/
integrations/
  clickfunnels/
shared/
```

---

## Clean Architecture Rules

### API Layer
Django Ninja routers handle validation and response orchestration. No business logic in routers.

### Services
All business rules and logic belong in Services.
**Required Services:**
* `ConfigurationService`
* `ContactService`
* `CourseService`
* `EnrollmentService`
* `ClickFunnelsClient` (Integration)

**Dependency Injection:** Services should receive external dependencies through constructor arguments where practical to improve testing.

### Integrations
All CF API communication is encapsulated in `integrations/clickfunnels/`. No raw requests elsewhere.

### Models
Models represent persistence only. Business logic stays in services.

---

## Logging Requirements
Every service and `ClickFunnelsClient` method must log:
* **start**
* **success**
* **failure** (using `logger.exception`)

**Security:** Never log secrets, tokens, auth headers, full contact payloads, full payment payloads, or full order payloads. 
**Safe to Log:** `workspace_id`, `contact_id`, `course_id`, `enrollment_attempt_id`.

---

## Testing Rules
Required tests:
* **Service tests** (Logic verification)
* **API tests** (Request/Response validation)
* **ClickFunnels client tests** (Using mocks)
* **Logging security tests** (Verify no secrets or sensitive payloads are leaked in logs)

Avoid brittle implementation tests. Test behavior.

---

## ClickFunnels Integration
* **Single Source of Truth:** `ClickFunnelsClient`.
* **Auth:** Authentication implementation must be verified against ClickFunnels API v2 docs before coding. Auth logic must remain isolated inside `ClickFunnelsClient`.
* **Syncing:** Manual only via Admin actions or API endpoints.

---

## Enrollment Flow
1. Verify contact exists (locally/remotely).
2. Create contact if missing.
3. Enroll contact in ClickFunnels.
4. Log result to `stdout`.
5. Persist `EnrollmentAttempt` record in DB.

---

## Definition of Done
* Code follows naming conventions.
* Tests (Service, Client Mocks, API, Logging) pass.
* Logging (Start/Success/Failure) exists for all methods.
* Secrets are masked in Admin and never logged.
* No duplicated logic.


## External API Contract Rule

For ClickFunnels API code, never infer response field types.

Use official docs and captured raw responses as the source of truth.

Every DTO must have:
- a documented sample response
- a validation test using that sample
- extra fields allowed
- optional fields marked optional

If a response field is unknown, preserve it in raw_payload rather than inventing a schema.