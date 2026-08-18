"""
Django settings for the TCS OS project.

Shared settings for ALL modules (admissions, and future apps like hr/finance).
Module-specific config belongs in that module's own app, not here.
"""

from pathlib import Path
import environ

BASE_DIR = Path(__file__).resolve().parent.parent

# django-environ reads from a .env file locally, and from real env vars
# (injected by Cloud Run from Secret Manager) in production.
# Nothing sensitive is hardcoded here — see docs/shared-stack.md.
env = environ.Env(
    DEBUG=(bool, False),
)
environ.Env.read_env(BASE_DIR / ".env")  # no-op if .env doesn't exist (e.g. in production)

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")

# Cloud Run service URL and/or the Cloudflare-proxied domain(s) go here.
# Required explicitly — Django silently 400s on requests to unlisted hosts.
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

# Needed separately from ALLOWED_HOSTS for POST/admin form submissions to work
# behind Cloudflare's proxy (must include scheme, e.g. https://admissions.tcsch.edu.gh)
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])


# Application definition

INSTALLED_APPS = [
    # Branded admin theme — must come before django.contrib.admin
    "unfold",
    "unfold.contrib.filters",

    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    "rest_framework",
    "corsheaders",

    # TCS OS modules — one Django app per module, sharing this one project
    "admissions",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "tcs_os.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "tcs_os.wsgi.application"


# Database — Supabase Postgres, one shared instance for all of TCS OS.
# DATABASE_URL comes from env (Secret Manager in production, .env locally).
DATABASES = {
    "default": env.db("DATABASE_URL")
}


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


# Static files — served via whitenoise from inside the Cloud Run container
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# CORS — locked to real known frontend origins only, set via env.
# e.g. CORS_ALLOWED_ORIGINS=https://admissions.tcsch.edu.gh
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])


# Django REST Framework — sensible defaults for Phase 1.
# Public endpoints (like /api/admissions/inquiries/) override permissions per-view.
REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "20/hour",
    },
}


# django-unfold — branded admin theme (TCS colours/logo to be added)
UNFOLD = {
    "SITE_TITLE": "TCS OS Admin",
    "SITE_HEADER": "TCS OS",
}


# Email — swap to a real backend (SMTP/SendGrid/etc.) when comms are built in Phase 2+
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
