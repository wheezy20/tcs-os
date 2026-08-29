# TCS OS — Shared Stack & Infrastructure

**Status:** active reference, applies to every module. Module-specific schema/decisions live in each module's own `02-stack-and-schema.md` instead.

## Stack

| Layer | Choice | Notes |
|---|---|---|
| Backend | **Django**, single project | One project, one app per module (`admissions`, `hr`, `finance`, ...) |
| API | **Django REST Framework** | Per-module endpoints, shared auth |
| Database | **Supabase (Postgres)** | One Supabase project, one Postgres instance for all of TCS OS. Used as plain hosted Postgres via connection string, Django owns all DB access. Supabase's own Auth/PostgREST layer is **not** used, to avoid two systems doing RBAC |
| File storage | **Supabase Storage** | Shared project, buckets can be namespaced per module (e.g. `admissions-documents`) |
| Hosting | **Google Cloud Run** | Django app containerised (Docker), deployed as one Cloud Run service for the whole project. Chosen over Railway/Render deliberately for the containerisation/IAM/deploy-pipeline experience, relevant to Eyram's cloud/data-scientist skill-building goals. Scales to zero, pay-per-use |

## Supabase project setup

When creating the Supabase project, leave these three options **unchecked**:

- **Enable Data API** — this is Supabase's own PostgREST layer for client libraries like `supabase-js` to query the database directly. Not used here, Django owns all database access via the Postgres connection string.
- **Automatically expose new tables** — only relevant if the Data API is on. Irrelevant here.
- **Enable automatic RLS** — Row Level Security is a Postgres-level access control feature for untrusted clients querying directly. Django connects with full access and does its own permission checks in code (see RBAC below), RLS would do nothing useful or could silently block Django's own queries if misconfigured.

Net: Django is the only thing that talks to the database. Supabase is used purely as hosted Postgres + Storage.
| Domain / edge | **Cloudflare** | Already owns the TCS domain(s). Proxy for TLS + DDoS, rate limiting rules per subdomain/route, Turnstile on any public-facing form |
| Admin dashboard | **Django admin**, themed with **django-unfold** | One branded admin for all modules; each app's models register into it |

## Deployment (Cloud Run)

**Step-by-step commands: `docs/deployment.md`.** The notes below are the standing architecture; that file is the how-to.

- Django app packaged as a single Docker image, one Cloud Run service for the whole `backend/` project (all module apps included, since it's one Django project).
- Connects to Supabase Postgres over the standard connection string, same as local dev, no Cloud SQL needed.
- Secrets (DB URL, Supabase keys, `SECRET_KEY`) stored in **Google Secret Manager**, injected as env vars at deploy time, not baked into the image. `settings.py` reads them via `django-environ` (e.g. `env('DATABASE_URL')`), same pattern locally from a gitignored `.env` file and in production from Secret Manager, never hardcoded either place.
- `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` must be set explicitly once the Cloud Run service URL and/or Cloudflare-proxied domain is known, this is easy to forget and causes a silent 400 if missed.
- `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")` is set unconditionally in `settings.py` — required because Cloud Run terminates TLS and forwards to the container over plain HTTP, only signaling the original scheme via that header. `SECURE_SSL_REDIRECT`/`SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE` are all gated on `not DEBUG` (never a separate env var — `DEBUG` is already this project's local-dev-vs-production signal), since local dev has no TLS listener at all and forcing HTTPS-only behavior there would just break `manage.py runserver`. Setting `SECURE_SSL_REDIRECT = True` *without* `SECURE_PROXY_SSL_HEADER` configured is the classic Cloud-Run-behind-Cloudflare redirect loop — Django can't see past the internal HTTP hop, thinks every request is insecure, and redirects it right back to itself — so these three always ship together, never one without the others. `SECURE_HSTS_SECONDS` deliberately left unset for now — a long max-age is hard to undo if anything ever goes wrong with the cert; revisit once the above has been stable in production for a while.
- Static files served via `whitenoise` (simplest option for a Cloud Run container) or a Cloud Storage bucket if that becomes a bottleneck later.
- Cloudflare DNS for `admissions.tcsch.edu.gh` (and future subdomains) points at the Cloud Run service URL.
- Redeploys via `gcloud run deploy` from the built image, or a simple GitHub Action later once the workflow is comfortable manually.

## Shared auth & RBAC

- One `User` model for all of TCS OS (Django's built-in, extended as needed).
- Roles/permissions via Django Groups/Permissions, shared across modules but scoped per app (e.g. an "Admissions Officer" group only gets permissions on admissions models).
- `django-guardian` available if/when a module needs object-level (per-record) permissions, not used by default.
- Secrets via environment variables (`django-environ`), never committed.
- CORS via `django-cors-headers`, locked to actual known frontend origins.

## Frontend pattern

Public-facing forms/portals per module are built with Lovable and/or Claude-generated UI, and submit to that module's DRF endpoints.

**Revised 2026-08-19:** originally these lived outside the Django project entirely (a separate static host). As of admissions going to real deployment, they're served by Django itself via plain `TemplateView` routes (e.g. `/inquiry`, `/apply`, `/offer` — see `docs/admissions/04-build-log.md`), so the whole module — API, admin, and public forms — sits behind the one subdomain with no separate hosting/CORS surface to manage. The HTML/CSS/JS itself is still hand-built, dependency-free, no build step; only *where it's served from* changed. Future modules should follow the same pattern rather than standing up a separate static host per module.

## Per-module folders

Each module gets:
- `backend/<module>/` — the Django app (models, admin, api)
- `docs/<module>/` — the five-file doc pattern (README, vision, stack-and-schema, build-order, build-log)
