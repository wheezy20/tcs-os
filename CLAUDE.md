# TCS OS — Instructions for Claude Code

This file is auto-loaded at the start of every Claude Code session in this
repo. Keep it short and load-bearing — anything long-form lives in `docs/`
and is linked from here, not duplicated here.

## Project docs — read these first

1. **`docs/PLAN.md`** — the full roadmap: what's done, what phase is
   active, what's next. Check this before starting any new work — only
   build what the active phase calls for.
2. **`docs/DESIGN.md`** — architectural and schema conventions. Anything
   load-bearing about how a table, a permission check, or a calculation
   is supposed to work lives here, not scattered across commit messages.
3. **`docs/CONSTRAINTS.md`** — hard rules, go-live checklist, known gaps
   and deliberate deferrals. Check before assuming something is safe to
   build a certain way.
4. **`docs/JOURNAL.md`** — dated log of every build session, across every
   module. Read the tail of this to see what just happened; append to it
   at the end of every session, before finishing.
5. **`docs/shared-stack.md`** — cross-module infrastructure: Django
   project setup, Supabase, hosting, Cloudflare, auth/RBAC pattern.
6. **`docs/admissions/`** — the admissions module's own five-file doc set
   (vision, schema, build order, build log). Self-contained; only read
   when working inside that module.

**Standing instruction: keep the docs current.** Scope, constraint,
stack, or design decisions get written to the relevant doc in the same
session they're made — don't wait to be asked. `JOURNAL.md` always gets
a dated entry before a session ends, even a short one.

## Repo layout

```
tcs-os/
  CLAUDE.md              # this file — repo root, auto-loaded by Claude Code
  backend/
    manage.py
    tcs_os/              # the Django project package (settings, root urls)
    modules/             # every module app lives here — this is where to
      __init__.py        # look first for "where's the code for X"
      admissions/        # a Django app; AppConfig.name = "modules.admissions",
      hr/                # label stays "admissions"/"hr" (migrations, perms,
                          # app_label lookups all still use the short label)
    templates/
    staticfiles/         # collectstatic output — generated, gitignored
  docs/
    README.md
    PLAN.md
    DESIGN.md
    CONSTRAINTS.md
    JOURNAL.md
    shared-stack.md
    deployment.md
    admissions/          # admissions module's own 5-file doc set
```

One Django project, one app per module (under `backend/modules/`), one
shared User/Role model — see `docs/shared-stack.md` for the full
architecture rule. Don't create a second Django project or a separate
backend for a new module. A new module's app lives in `backend/modules/
<name>/`; its import path is `modules.<name>`; its `AppConfig.name` and
`INSTALLED_APPS` entry both use that full path, but its app **label**
(migrations, `app_label` lookups, `user.has_perm("<name>.xyz")`) stays
just `<name>` — Django derives this automatically from the last path
component, so don't override it.

## Workflow

- Claude Code (VS Code extension) handles file writing, local testing,
  and local git commits.
- Eyram reviews Claude Code's plan/summary output and pastes
  architectural decisions back into chat (Claude, in claude.ai) for
  review before they're treated as settled.
- Eyram runs all terminal commands with real infrastructure side effects
  himself: `git push`, `gcloud builds submit`, `gcloud run deploy`,
  `gcloud run jobs execute`. Claude Code does not push to GitHub or touch
  production — no credentials for either in its environment.
- Build one module or feature at a time, scoped and reviewable — not
  sweeping multi-module changes in a single session.
- Eyram is early in his data science / programming learning curve. This
  doesn't change the standard the code is held to, but explanations of
  *why* a decision was made are worth keeping in the docs rather than
  assuming context carries in one person's head.

## Deploy sequence (always, in this order)

1. Build image via Cloud Build.
2. Run the relevant `*-migrate` Cloud Run Job.
3. `gcloud run deploy` with `--update-env-vars` (never `--set-env-vars` —
   it wipes every existing env var not explicitly listed).

## Hard-won lessons (see `docs/DESIGN.md` and `docs/CONSTRAINTS.md` for
## the full writeups — this is just the index so a session doesn't repeat
## a mistake already paid for once)

- Never use `--set-env-vars` on `gcloud run deploy` — always
  `--update-env-vars`.
- Bulk Django admin actions (`queryset.update()`) bypass `Model.save()` —
  a reference-number assignment, an audit trigger, or any other
  save()-time side effect silently never fires. Iterate and call
  `.save()` per row instead.
- Batch email sends must be atomic at the individual message level, not
  the batch level — one bad address can't be allowed to fail an entire
  send.
- File upload validation (type, size) must be enforced at all three
  layers: client-side, serializer, and the storage bucket itself — the
  bucket-level check is the only one a client can't bypass.
- Transactional email must never block the HTTP response — decouple via
  a task queue.
- A statutory or regulatory rate structure (tax, pension, deduction
  splits) must be verified against an authoritative source or a working
  reference implementation before being changed — not against a
  conversational restatement that hasn't itself been checked. See
  `docs/DESIGN.md`'s payroll section for exactly what this cost once.
- "The file exists, has the right size, and has the right magic bytes"
  is not evidence a generated binary document (PDF, export) is correct —
  only opening and looking at it is.