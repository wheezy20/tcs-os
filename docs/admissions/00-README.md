# TCS Admissions Module — Documentation Index

Part of TCS OS. Read `../shared-stack.md` first for infrastructure shared across all modules (Django project setup, auth/RBAC, Supabase, hosting, Cloudflare). This folder covers what's specific to admissions.

Read in this order:

1. **01-vision.md** — full long-term vision and domain model for admissions. Reference, not a build list. Rarely changes.
2. **02-stack-and-schema.md** — admissions-specific schema and API surface. Assumes the shared stack from `../shared-stack.md`.
3. **03-build-order.md** — the phased plan, one phase at a time. Active work plan, update as phases complete.
4. **04-build-log.md** — dated journal of build sessions. Append after every session.

## Rule for Claude Code sessions

Only build what the **current phase** in `03-build-order.md` calls for. `01-vision.md` describes real future intent, not a build list for today. If a session finds itself reaching for models or features outside the active phase, stop and check `03-build-order.md` first.

At the end of a work session, add an entry to `04-build-log.md` before finishing.
