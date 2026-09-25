import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "django-insecure-default-key-for-dev")

DEBUG = os.getenv("DEBUG", "True") == "True"

ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "").split(",") if os.getenv("ALLOWED_HOSTS") else []

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

# ValuedCRM Security
FIELD_ENCRYPTION_KEY = os.getenv("FIELD_ENCRYPTION_KEY")

# Branding — configurable per deployment, never hardcoded in templates (see AGENT.md).
# BRAND_NAME: this tool's own identity, shown on staff-facing surfaces (portal chrome,
# login page, page titles). BUSINESS_NAME: the storefront/tenant name customers
# recognize, shown on the public guest checkout (templates/public_base.html and its
# children) — this deployment sells courses for Monafrolibre, not for "this CRM tool".
BRAND_NAME = os.getenv("BRAND_NAME", "Valued Haircare")
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "Monafrolibre")

# Canonical public HTTPS base URL for this application (no trailing slash),
# e.g. "https://checkout.example.com". Required by apps.payments checkout
# (Phase 5) to construct Tara's application-owned webHookUrl/returnUrl safely
# — never derived from the request's Host header. Empty by default; checkout
# fails closed (raises CheckoutConfigurationError) if this isn't set, rather
# than guessing a host.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

INSTALLED_APPS = [
    "core.apps.RestrictedAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_extensions",
    "shared",
    "apps.configuration",
    "apps.contacts",
    "apps.courses",
    "apps.enrollments",
    "apps.dashboard",
    "apps.payments",
    "apps.provisioning",
    "apps.operations",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "shared.context_processors.branding",
            ],
        },
    },
]

WSGI_APPLICATION = "core.wsgi.application"

# Django's default messages framework tags "error" doesn't match any
# Bootstrap alert class (Bootstrap only has "alert-danger") — without this
# mapping, error flash messages render with no color styling at all.
from django.contrib.messages import constants as message_constants  # noqa: E402

MESSAGE_TAGS = {
    message_constants.ERROR: "danger",
}

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME", "valuedcrm"),
        "USER": os.getenv("DB_USER", "postgres"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "localhost"),
        "PORT": os.getenv("DB_PORT", "5432"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "fr"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Files uploaded by staff (sales-page images). Served by Django itself, see core/urls.py.
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# /login/ — the portal's own sign-in, used by every @login_required staff
# view. /admin/login/ still exists separately for superusers only (see
# shared/admin_site.py, AGENT.md) — never the default for team members.
LOGIN_URL = "/connexion/"
LOGIN_REDIRECT_URL = "/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Phase 7 (docs/TARA_INTEGRATION_PROJECT.md): production delivery config for
# payment-confirmation email, driven entirely from the environment — no
# hard-coded SMTP credentials. Defaults to the console backend (safe/no-op in
# local dev) rather than guessing a real SMTP host. Test runs never use these:
# pytest-django forces EMAIL_BACKEND to locmem for every test regardless of
# this setting, so no automated test ever sends real email.
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True") == "True"
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "no-reply@valuedcrm.example")

# Phase 5: checkout is this app's first guest-facing (non-staff) surface —
# harden session/CSRF cookies for production the same way ALLOWED_HOSTS/DEBUG
# already gate other production-only behavior. No effect in local DEBUG runs
# (plain HTTP) so existing dev workflows are unaffected.

# Apache terminates HTTPS and forwards requests to Gunicorn over HTTP.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Unpaid checkout orders are marked EXPIRED by the hourly reconciliation
# (apps/payments/services.py::OrderExpiryService): after
# ORDER_PENDING_EXPIRY_DAYS, or after ORDER_FAILED_CHECKOUT_EXPIRY_MINUTES
# when Tara never even issued a payment link. Tara documents no link
# validity period — align the day count with Tara once known.
ORDER_PENDING_EXPIRY_DAYS = int(os.getenv("ORDER_PENDING_EXPIRY_DAYS", "7"))
ORDER_FAILED_CHECKOUT_EXPIRY_MINUTES = int(os.getenv("ORDER_FAILED_CHECKOUT_EXPIRY_MINUTES", "60"))

# Number of reverse proxies in front of Gunicorn that append to
# X-Forwarded-For (Apache = 1). 0 keeps REMOTE_ADDR, correct only without a
# proxy — behind Apache it would turn every per-IP rate limit into one global
# bucket. See shared/ratelimit.py.
TRUSTED_PROXY_COUNT = int(os.getenv("TRUSTED_PROXY_COUNT", "0"))
RATELIMIT_IP_META_KEY = "shared.ratelimit.client_ip"

SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

SECURE_CONTENT_TYPE_NOSNIFF = not DEBUG
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"


# Logging configuration with file support
LOG_DIR = BASE_DIR / "logs"
os.makedirs(LOG_DIR, exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "secret_scrubber": {
            "()": "shared.logging_utils.SecretScrubberFilter",
        },
    },
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
            "filters": ["secret_scrubber"],
        },
        "app_file": {
            "class": "logging.FileHandler",
            "filename": LOG_DIR / "app.log",
            "formatter": "verbose",
            "filters": ["secret_scrubber"],
        },
        "clickfunnels_file": {
            "class": "logging.FileHandler",
            "filename": LOG_DIR / "clickfunnels.log",
            "formatter": "verbose",
            "filters": ["secret_scrubber"],
        },
        "errors_file": {
            "class": "logging.FileHandler",
            "filename": LOG_DIR / "errors.log",
            "formatter": "verbose",
            "filters": ["secret_scrubber"],
            "level": "ERROR",
        },
    },
    "root": {
        "handlers": ["console", "app_file", "errors_file"],
        "level": "INFO",
    },
    "loggers": {
        "integrations.clickfunnels": {
            "handlers": ["console", "clickfunnels_file", "errors_file"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
