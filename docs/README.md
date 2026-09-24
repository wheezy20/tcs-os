# TCS OS — Documentation Index

TCS OS is a single Django backend serving all Treasures Christian School internal systems, built module by module as Django apps sharing one project, one User/Role model, and one database.

## Read order

1. **`../CLAUDE.md`** (repo root) — auto-loaded by Claude Code at the start of every session. Points at everything below; read this first if you're starting fresh.
2. **PLAN.md** — the full roadmap: what's done, what phase is active, what's next. Check before starting any new work.
3. **DESIGN.md** — architectural, schema, and branding conventions that apply across every module.
4. **CONSTRAINTS.md** — hard rules, go-live checklist, known gaps and deliberate deferrals.
5. **JOURNAL.md** — dated log of every build session, across every module. Read the tail to see what just happened; append before ending a session.
6. **shared-stack.md** — decisions that apply across every module: the Django project itself, shared auth/RBAC/User model, the Supabase project, hosting, and Cloudflare setup. Read this once, it doesn't repeat per module.
7. **deployment.md** — the Cloud Run deployment runbook.
8. **`<module>/`** — one folder per module (e.g. `admissions/`). Each follows the same five-file pattern: `00-README.md`, `01-vision.md`, `02-stack-and-schema.md`, `03-build-order.md`, `04-build-log.md`. A module's own `02-stack-and-schema.md` only covers what's specific to that module (its models, its endpoints), not shared infrastructure — that's in `shared-stack.md` — and not a cross-module convention, which belongs in the system-wide `DESIGN.md` instead.

## Modules

- **admissions** — Admissions, Enrolment & Advancement. Done, live in production. See `docs/admissions/00-README.md`.
- **hr** — Employees, payroll, statutory deductions. In progress, being ported from the standalone TCS ERP. See `PLAN.md`'s "ERP merge" section for the current phase.
- *(future modules go here as they're started — finance, students, academics, etc.)*

## Architecture rule

One Django project (`backend/`), one shared User/Role/Permission model. Every module is a Django app inside that project (`backend/admissions/`, `backend/hr/`, etc.), not a separate backend. This avoids rebuilding auth and RBAC per module. See `DESIGN.md` for the full RBAC and schema-convention detail.