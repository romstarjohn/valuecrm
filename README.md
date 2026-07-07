# ValuedCRM MVP

ValuedCRM is a focused Django-based admin application designed to bridge external contact management with ClickFunnels v2 course enrollments. It prioritizes simplicity, security, and a robust audit trail.

## Key Features
- **ClickFunnels v2 Integration:** Seamlessly sync courses and manage contact enrollments using API Access Tokens.
- **Security-First:** At-rest credential encryption (Fernet), mandatory User-Agent headers, and automated log scrubbing.
- **Protected API:** All Ninja API endpoints are secured via Django Session Authentication.
- **Admin-Centric UI:** Full management of contacts and courses via the standard Django Admin.
- **Bulk Enrollment:** Synchronous bulk enrollment for up to 50 students per request.
- **Audit Logs:** Read-only tracking of every enrollment attempt with detailed error logs.

## Technology Stack
- **Python:** 3.13+
- **Framework:** Django 5.1
- **API:** Django Ninja
- **Database:** PostgreSQL
- **Security:** Cryptography (Fernet)

## Documentation Index
1. [**Setup Guide**](docs/SETUP.md): Virtual environments, Docker, and environment configuration.
2. [**API Reference**](docs/API.md): REST API endpoints for configuration, contacts, courses, and enrollments.
3. [**Operational Guide**](docs/OPERATIONS.md): How to generate CFv2 tokens, sync courses, and manage enrollments.
4. [**Architecture**](docs/ARCHITECTURE.md): System design and data flow.
5. [**QA Checklist**](docs/QA_CHECKLIST.md): Verification steps for production readiness.

## Quick Start
To get the project running locally, follow the [**Setup Guide**](docs/SETUP.md).

```bash
# Basic test run
pytest --nomigrations
```

## Maintenance
For logging and troubleshooting, see the [**Operations Guide**](docs/OPERATIONS.md).
# valuecrm
