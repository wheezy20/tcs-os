# TCS Admissions Module — Build Log

Append-only. Add a new dated entry after every work session: what was built, decisions made mid-session, what's left open for next time. Do not edit past entries.

---

## 2026-08-17 — Planning session
- Defined stack: Django + DRF + Supabase (Postgres + Storage) + Lovable/Claude for frontend + Cloudflare (domain already owned) + django-unfold for branded admin
- Confirmed RBAC lives entirely in Django (Supabase used as a plain DB/storage backend, not through its own Auth/RLS layer)
- Restructured docs into TCS OS-wide layout: one shared-stack.md at the org level, per-module doc folders (README, vision, stack-and-schema, build-order, build-log)
- Confirmed architecture: one Django backend (`backend/`), one app per module, not separate backends per module
- No code written yet. Next session should start Phase 1.

## 2026-08-17 — Hosting decision
- Switched hosting from Railway/Render to Google Cloud Run, deliberate choice for the containerisation/IAM/deploy experience over setup speed
- Updated `docs/shared-stack.md` with a Deployment section (Secret Manager for secrets, whitenoise for static files, Supabase connection unchanged)
- Added starter `backend/Dockerfile`, `requirements.txt`, `.dockerignore`
- Still no Django project scaffolded yet, next session: `django-admin startproject`, then start on Phase 1 models
