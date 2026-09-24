---
name: deploy-checklist
description: Walk through TCS OS's Cloud Run deploy sequence step by step, confirming each prerequisite and step before moving to the next. Use whenever asked to deploy, redeploy, ship, or push changes live for any TCS OS service. Prepares and verifies commands — does not execute them, per CLAUDE.md's workflow (Eyram runs every command with real infrastructure side effects himself).
---

# Deploy checklist

## Before anything else

Read `CLAUDE.md`'s Workflow section. Eyram runs every command with real
infrastructure side effects himself — `git push`, `gcloud builds submit`,
`gcloud run deploy`, `gcloud run jobs execute`. **This skill's job is to
prepare and verify the exact, correct command sequence — not to run it.**
Present the commands in order for Eyram to run; do not execute them via
Bash yourself, even if the tool technically permits it.

If a `PreToolUse` hook is installed (`.claude/hooks/block-set-env-vars.sh`)
it will refuse a `--set-env-vars` command anyway — but the goal here is to
never hand Eyram a wrong command in the first place, not to rely on the
hook as the only safety net.

## The sequence, always in this order

1. **Check for pending migrations.** For every module whose models
   changed this session, run locally:
   ```
   cd backend && python manage.py makemigrations --check --dry-run
   ```
   If anything's pending, stop and generate the migration first — never
   let an unmigrated model change ride along inside an image build
   undetected.

2. **Confirm the target service and its current state** before touching
   anything. Pull the real, current values from `docs/deployment.md`
   (image tag/digest convention, migrate job name, region, the full list
   of env vars already set) rather than assuming — these are per-service
   and documented there, and guessing at any of them risks a deploy that
   silently diverges from what `deployment.md` says is true. If deploying
   a service for the **first time** (no existing Cloud Run service,
   e.g. a future `hr` service that doesn't exist yet), stop here — a
   first deploy needs Secret Manager entries, IAM bindings, and service
   creation set up first. This checklist assumes an existing, previously
   deployed service; it is not a from-scratch setup guide.

3. **Build the image.**
   ```
   gcloud builds submit backend/ --tag <image>
   ```
   Confirm the exact image path/tag against `docs/deployment.md` for the
   service being deployed, not a guessed value.

4. **Run the migrate job**, using the freshly built image, for every
   module with a pending migration from step 1:
   ```
   gcloud run jobs execute <service>-migrate --region=<region> --wait
   ```
   Confirm the output actually says "Applying migration ... OK" (or "No
   migrations to apply" if there genuinely are none) — don't move on
   assuming it worked from the exit code alone.

5. **Re-run any one-off config job** this deploy affects (e.g.
   `configure_storage_bucket` if upload limits or allowed types changed).
   Check `docs/deployment.md`'s per-step notes for the service in
   question for whether one applies here.

6. **Deploy**, always with `--update-env-vars`, never `--set-env-vars`
   or `--set-env-vars-file`:
   ```
   gcloud run deploy <service> --image=<image> --region=<region> \
     --update-env-vars="KEY=value,..."
   ```
   List only the env vars that are actually changing for this deploy —
   everything else stays untouched under `--update-env-vars`, which is
   the entire point of using it over `--set-env-vars`.

7. **Verify, don't assume.**
   ```
   gcloud run services describe <service> --region=<region>
   ```
   Confirm: the new revision is the one serving traffic, the env var
   count still looks right (a sudden drop to a handful of vars is an
   early warning sign `--set-env-vars` got used somewhere upstream of
   this deploy), and a real request against the live URL returns the
   expected result — not just a 200, the actual expected content/behavior
   for whatever changed.

## If any prerequisite isn't met

Stop and say so plainly, rather than presenting a command that will fail
or — worse — half-succeed (e.g. a deploy that goes out before its
migration has actually run). A checklist that gets silently skipped past
a broken step defeats the purpose of having one.
