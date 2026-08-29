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

# Cloud Run terminates TLS and forwards to this container over plain HTTP,
# only signaling the original scheme via this header — without telling Django
# to trust it, request.is_secure() always reads False, and SECURE_SSL_REDIRECT
# below would redirect every already-HTTPS request right back to itself
# (Cloudflare -> Cloud Run -> Django redirect -> Cloudflare -> ... forever).
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# All three gated on `not DEBUG`, not a separate env var — DEBUG is already
# this project's local-dev-vs-production signal (True locally, False on Cloud
# Run), and local dev has no TLS listener at all, so forcing these on there
# would just break `manage.py runserver` outright rather than protect anything.
SECURE_SSL_REDIRECT = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG


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
        # Project-level overrides (e.g. templates/admin/login.html, which extends
        # and re-brands unfold's own admin/login.html) — DIRS is searched before
        # app template dirs, so this correctly shadows unfold's version.
        "DIRS": [BASE_DIR / "templates"],
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
#
# DEFAULT_AUTHENTICATION_CLASSES is deliberately empty: every DRF view in this
# project is AllowAny (Django admin, a separate surface, handles all
# authenticated work). Leaving DRF's own default (SessionAuthentication) in
# place caused a real production bug — SessionAuthentication only enforces
# CSRF when request.user is an *authenticated* user, which happens whenever
# the same browser also has a logged-in admin session (cookies are
# domain-wide, not path-scoped) — e.g. staff testing the public form in one
# tab while logged into /admin/ in another. The public forms' plain fetch()
# calls were never built to send a CSRF token, since the endpoints are meant
# to be anonymous, so that combination 403'd with "CSRF cookie not set" for
# real staff testing the real form, root-caused via a local repro (an
# authenticated Client got 403, an anonymous one got the correct 400).
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "20/hour",
        # Draft save/resume (Phase 5's free section navigation autosaves on
        # every jump, not just "Next") is meant to be called often — this
        # exists to guard the real submission endpoints from abuse, not to
        # throttle normal use of the thing it was built for. See
        # admissions.views.DraftRateThrottle.
        "application_draft": "120/hour",
    },
}


# django-unfold — branded admin theme.
# Colors: 11-stop "primary" ramp interpolated between actual brand colors
# (Jungle Mist -> Deep Jungle Green -> Deep Teal Shadow -> near-black), not
# invented — see docs/admissions/brand-tokens.md. unfold accepts plain hex
# here (unfold.utils.convert_color handles the conversion).
# Logo/icon: callables in admissions/branding.py, not hardcoded path strings —
# see that module's docstring for why (staticfiles manifest hashing).
UNFOLD = {
    "SITE_TITLE": "Treasures Christian School — Admin",
    "SITE_HEADER": "Treasures Christian School",
    "SITE_SUBHEADER": "Admissions",
    "SITE_URL": "/",
    "SITE_LOGO": {
        "light": "admissions.branding.logo_light",
        "dark": "admissions.branding.logo_dark",
    },
    "SITE_ICON": {
        "light": "admissions.branding.favicon",
        "dark": "admissions.branding.favicon",
    },
    "SITE_FAVICONS": [
        {
            "rel": "icon",
            "sizes": "32x32",
            "type": "image/png",
            "href": "admissions.branding.favicon",
        },
    ],
    "COLORS": {
        "primary": {
            "50": "#E6F3F3",
            "100": "#C0DADB",
            "200": "#99C1C2",
            "300": "#73A8AA",
            "400": "#4D9092",
            "500": "#267779",
            "600": "#005E61",  # Deep Jungle Green — brand anchor
            "700": "#05565A",
            "800": "#094F53",
            "900": "#0A373A",
            "950": "#081415",
        },
    },
}

# Only the branded Django admin exists as a login-gated surface right now, so
# send everyone there after login instead of Django's default /accounts/profile/,
# which 404s (no such view/page exists in this project).
LOGIN_REDIRECT_URL = "/admin/"

# Email — Resend via its SMTP relay (no extra dependency: Django's built-in
# smtp backend works as-is, Resend just needs "resend" as the username and
# the API key as the password). Falls back to the console backend — prints
# to stdout, sends nothing — whenever RESEND_API_KEY is unset, so local
# dev and CI never need real credentials.
RESEND_API_KEY = env("RESEND_API_KEY", default="")
if RESEND_API_KEY:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = "smtp.resend.com"
    EMAIL_PORT = 587
    EMAIL_USE_TLS = True
    EMAIL_HOST_USER = "resend"  # literal string Resend requires, not a placeholder
    EMAIL_HOST_PASSWORD = RESEND_API_KEY
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="admissions@tcsch.edu.gh")

# Supabase Storage — used to mint signed upload/download URLs for admissions documents.
# Module-specific (only admissions uses it today), but the credentials are the same
# Supabase project as DATABASE_URL so they live at the project level, not per-app.
SUPABASE_URL = env("SUPABASE_URL", default="")
SUPABASE_SERVICE_ROLE_KEY = env("SUPABASE_SERVICE_ROLE_KEY", default="")
SUPABASE_STORAGE_BUCKET = env("SUPABASE_STORAGE_BUCKET", default="admissions-documents")

# Enforced in three places, in increasing order of trust: the browser (instant
# feedback, trivially bypassed), UploadURLRequestSerializer (rejects a bad
# request before a signed URL is even minted, but trusts the client-declared
# file_size), and the Supabase bucket's own file_size_limit/allowed_mime_types
# (set via `manage.py configure_storage_bucket` — see admissions/storage.py) —
# the only layer that checks the real bytes on the actual PUT, so it can't be
# bypassed by a client lying in the JSON request.
MAX_UPLOAD_SIZE_MB = env.int("MAX_UPLOAD_SIZE_MB", default=10)

# Cloudflare Turnstile — bot-blocking on the three public forms (inquiry,
# application, offer-response). Defaults are Cloudflare's own published test
# keys (always-passes site key + matching always-passes secret key — see
# https://developers.cloudflare.com/turnstile/troubleshooting/testing/),
# not placeholders that fail closed — local dev and this env's own tests can
# exercise the real Cloudflare siteverify round trip with no account of its
# own. **Must be overridden with real keys from a real Turnstile site before
# this offers any actual bot protection** — the test keys are public
# knowledge, so leaving them in production verifies nothing.
TURNSTILE_SITE_KEY = env("TURNSTILE_SITE_KEY", default="1x00000000000000000000AA")
TURNSTILE_SECRET_KEY = env("TURNSTILE_SECRET_KEY", default="1x0000000000000000000000000000000AA")

# Phase 3 — offers. No scheduler exists in this project yet (no Celery, no cron,
# nothing deployed at all), so expiry is resolved lazily on read rather than by a
# timed job — see Offer.refresh_expiry() in admissions/models.py.
OFFER_EXPIRY_DAYS = env.int("OFFER_EXPIRY_DAYS", default=14)

# Base URL used to build the accept/decline link sent in the offer email.
# As of the /inquiry, /apply, /offer routing (see tcs_os/urls.py), the offer
# page is served by this same Django app, so the dev default is the Django
# dev server itself, not a separate static-file server. Set to the real
# subdomain (e.g. https://admissions.tcsch.edu.gh) once deployed.
FRONTEND_BASE_URL = env("FRONTEND_BASE_URL", default="http://127.0.0.1:8000")

# Phase 4 — email attachments (prospectus/brochure, etc.). Generic by design:
# a directory of files plus a per-email-type filename list, both empty/unset
# by default so nothing breaks before real files exist. Drop files into this
# directory and list their names in INQUIRY_EMAIL_ATTACHMENTS (comma-separated
# env var) to start attaching them — no code change needed. See
# admissions/emails.py.
ADMISSIONS_ATTACHMENTS_DIR = env("ADMISSIONS_ATTACHMENTS_DIR", default=str(BASE_DIR / "admissions" / "attachments"))
INQUIRY_EMAIL_ATTACHMENTS = env.list("INQUIRY_EMAIL_ATTACHMENTS", default=[])

# Phase 5 — expanded Application form.
#
# No scheduler exists in this project (see OFFER_EXPIRY_DAYS above) — draft
# expiry is resolved lazily the same way, via ApplicationDraft.is_expired.
# Longer than OFFER_EXPIRY_DAYS since gathering documents can genuinely take
# a parent weeks, not days.
DRAFT_EXPIRY_DAYS = env.int("DRAFT_EXPIRY_DAYS", default=30)

# Shown in the Documents & Payment step of the Application form and included
# in the application-submitted confirmation email. Env-var-driven so the
# real bank/mobile-money details can be updated without a code change.
APPLICATION_FEE_PAYMENT_INSTRUCTIONS = env(
    "APPLICATION_FEE_PAYMENT_INSTRUCTIONS",
    default=(
        "Once your application form is received and reviewed, you will be notified via "
        "e-mail regarding an admission decision after due process. Submission of an "
        "application form and payment of the application fee does NOT mean admission "
        "has been granted.\n\n"
        "ACCOUNT DETAILS:\n"
        "Consolidated Bank Ghana\n"
        "Account No: 0383011100001\n"
        "Name: Treasures Christian School\n\n"
        "PAYMENT PROCESS:\n"
        "Walk in to the bank, OR dial *170# → Option 1 (Transfer money) → Option 6 "
        "(Bank account) → Option 1 (Wallet to bank account) → # for next → "
        "Option 16 (CBG) → Enter account number 0383011100001 → Enter amount "
        "GHS 200.00 → Enter Reference: name of the learner"
    ),
)

# Phase 6 — bulk/marketing email (admissions/bulk_email.py). Uses the same
# RESEND_API_KEY as transactional email (it's a Bearer token for Resend's
# HTTP API here rather than an SMTP password, same credential either way),
# but a *separate* sending address/subdomain — bulk mail is far more likely
# to generate spam complaints/bounces than a one-off confirmation email, and
# a shared domain would let that damage the reputation of the offer/
# confirmation emails that actually matter. Must be a domain verified
# separately in Resend — see docs/deployment.md.
BULK_EMAIL_FROM_EMAIL = env("BULK_EMAIL_FROM_EMAIL", default="updates@updates.tcsch.edu.gh")

# Cloud Tasks — the actual background-job mechanism for dispatching a bulk
# send without a staff member watching a spinner for minutes (see
# docs/admissions/02-stack-and-schema.md for the throughput math: Resend's
# 10 req/sec team-wide limit makes a synchronous per-recipient loop
# infeasible at TCS's real family count). No task queue existed in this
# project before this — it's genuinely new infrastructure, not a re-use of
# an existing pattern like OFFER_EXPIRY_DAYS's lazy-expiry trick.
GCP_PROJECT_ID = env("GCP_PROJECT_ID", default="")
CLOUD_TASKS_LOCATION = env("CLOUD_TASKS_LOCATION", default="europe-west1")
CLOUD_TASKS_QUEUE = env("CLOUD_TASKS_QUEUE", default="admissions-bulk-email")
CLOUD_TASKS_MAX_ATTEMPTS = env.int("CLOUD_TASKS_MAX_ATTEMPTS", default=3)  # must match the queue's own --max-attempts

# The internal batch-send endpoint isn't a public API — Cloud Tasks is its
# only legitimate caller. Verified with a shared secret (constant-time
# compare, see views.py) rather than full OIDC verification: simpler, no
# extra dependency for the auth check itself, and appropriate for an
# endpoint nothing else needs to call. Empty by default so local dev/tests
# never accidentally ship a real secret — BulkEmailBatchSendView refuses all
# requests while this is unset (see its own docstring).
BULK_EMAIL_INTERNAL_SECRET = env("BULK_EMAIL_INTERNAL_SECRET", default="")
