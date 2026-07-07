# Setup Guide

This guide covers the local development setup for ValuedCRM.

## Prerequisites
- **Python 3.13+**
- **Docker** (for running PostgreSQL)
- **pip** (Python package manager)

## 1. Virtual Environment Setup

```bash
# Clone the repository and navigate into it
cd valuedcrm

# Create a virtual environment
python -m venv .venv

# Activate the virtual environment
# On macOS/Linux:
source .venv/bin/activate
# On Windows:
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

## 2. Environment Configuration

Create a `.env` file in the project root:

```env
DEBUG=True
DJANGO_SECRET_KEY=your-insecure-dev-key
FIELD_ENCRYPTION_KEY= # Generate this using the command below

# Database Configuration
DB_NAME=valuedcrm
DB_USER=postgres
DB_PASSWORD=postgres
DB_HOST=localhost
DB_PORT=5432
```

### Generate Encryption Key
Run this command and copy the output into your `FIELD_ENCRYPTION_KEY` in `.env`:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## 3. Run PostgreSQL with Docker

If you don't have a local PostgreSQL instance, run one via Docker:

```bash
docker run --name valuedcrm-db \
  -e POSTGRES_DB=valuedcrm \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -p 5432:5432 \
  -d postgres:16
```

## 4. Initialize Database

```bash
# Run migrations
python manage.py migrate

# Create an admin user
python manage.py createsuperuser
```

## 5. Start the Development Server

```bash
python manage.py runserver
```

The application will be available at:
- **Admin UI:** [http://127.0.0.1:8000/admin/](http://127.0.0.1:8000/admin/)
- **API Docs (Swagger):** [http://127.0.0.1:8000/api/docs](http://127.0.0.1:8000/api/docs)
  - *Note: API Docs require an active Admin session.*

## 6. Run Tests

```bash
pytest --nomigrations
```
