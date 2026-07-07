# QA Checklist

Use this checklist to verify the MVP for production readiness.

## 1. Environment & Infrastructure
- [ ] `.env` file exists and contains `FIELD_ENCRYPTION_KEY`.
- [ ] PostgreSQL is accessible and migrations are applied.
- [ ] `DJANGO_SECRET_KEY` is randomized.
- [ ] `DEBUG` is set to `False` for production.

## 2. Security & Compliance
- [ ] API Token is masked in Admin.
- [ ] All API endpoints (except `/health`) return `401` without an active session.
- [ ] Swagger Docs (`/api/docs`) are disabled when `DEBUG=False`.
- [ ] No raw tokens or contact emails appear in `stdout` logs.
- [ ] Fernet encryption is verified via `pytest tests/shared/test_security.py`.

## 3. Configuration & Integration
- [ ] "Verify Credentials" action correctly populates Workspace and Team IDs.
- [ ] Only one configuration can be active at a time.
- [ ] Mandatory `User-Agent` header is sent in every request.

## 4. Courses App
- [ ] "Sync Courses" action correctly updates existing courses and creates new ones.
- [ ] `cf_course_id` and `workspace_id` are readonly in Admin.

## 5. Enrollment Engine
- [ ] Single enrollment returns `SUCCESS` for new students.
- [ ] Bulk enrollment correctly handles partial failures (e.g., one student already enrolled).
- [ ] Every enrollment attempt persists an audit record.
- [ ] `MAX_BULK_ENROLLMENT_SIZE` (50) is enforced.

## 6. Automated Verification
Run the full test suite:
```bash
pytest --nomigrations
```

Confirmed pass count: **39 tests**.
