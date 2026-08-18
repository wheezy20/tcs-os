# TCS OS — Documentation Index

TCS OS is a single Django backend serving all Treasures Christian School internal systems, built module by module as Django apps sharing one project, one User/Role model, and one database.

## Read order

1. **shared-stack.md** — decisions that apply across every module: the Django project itself, shared auth/RBAC/User model, the Supabase project, hosting, and Cloudflare setup. Read this once, it doesn't repeat per module.
2. **`<module>/`** — one folder per module (e.g. `admissions/`). Each follows the same five-file pattern: `00-README.md`, `01-vision.md`, `02-stack-and-schema.md`, `03-build-order.md`, `04-build-log.md`. A module's own `02-stack-and-schema.md` only covers what's specific to that module (its models, its endpoints), not shared infrastructure, that's in `shared-stack.md`.

## Modules

- **admissions** — Admissions, Enrolment & Advancement. See `docs/admissions/00-README.md`.
- *(future modules go here as they're started — HR, finance, etc.)*

## Architecture rule

One Django project (`backend/`), one shared User/Role/Permission model. Every module is a Django app inside that project (`backend/admissions/`, `backend/hr/`, etc.), not a separate backend. This avoids rebuilding auth and RBAC per module.
