# TCS Admissions Module — Vision & Domain Model

**Status:** long-term reference. Not a build list for the current phase, check `03-build-order.md` for what's active now.

## Purpose

Not just an online form. Over time, this module should function as an integrated admissions CRM, application/document management system, assessment/interview tracker, decision and waitlist system, parent portal, communications system, and admissions analytics platform, eventually feeding into re-enrolment and alumni/advancement.

Benchmark: OpenApply-class admissions CRM, the kind Lincoln Community School and Tema International School run on today.

## Core design principle: family, not application

The system is built around a **Family**, not an application record. A parent may have multiple children, multiple applications over time, a currently enrolled child and an applying child simultaneously, and eventually become an alumni parent.

```
Family
 ├── Parent/Guardian
 ├── Parent/Guardian
 ├── Student
 │    └── Application
 ├── Student
 │    └── Application
 └── Communication History
```

## Full lifecycle (target state, all phases)

```
Marketing / Referral → Enquiry → Prospective Student Profile → Application
→ Application Fee → Document Collection → Completeness Check
→ Entrance Assessment → Interview → Admissions Review → Decision
   ├── Rejected
   ├── Waitlisted
   └── Accepted → Offer → Acceptance → Enrolment Payment → Enrolment
                → Student Record → Current Student → Re-enrolment
                → Graduation → Alumni/Advancement
```

## Full module list (target state — not all built yet)

CRM & lead management · Prospective family management · Application management · Dynamic application forms · Workflow engine · Checklists · Document management · Assessments · Interviews · School tours/open days · Admissions review (with rubrics) · Multi-stage review · Decisions · Waitlist · Capacity management · Offer & admission management · Enrolment · Parent portal · Communications & automation · Admissions payments (feeds central TCS finance, doesn't own its own ledger) · Re-enrolment · Marketing/campaign attribution · Admissions analytics · AI conversational analytics · RBAC · Multi-campus · Import/export · Duplicate detection · Audit & compliance · Alumni/advancement

**Parent portal — known gap, not yet built.** Phase 5's multi-step Application form added a token-based save/resume mechanism (`ApplicationDraft`, same trust model as the existing Offer accept/decline link — an unguessable token, no login) so a parent can pause and come back later. That's an explicit stopgap for not having a real parent portal, not a substitute for one — no authentication, no way for a parent to see their family's full history across Inquiry/Application/Offer in one place, no password reset flow, nothing that survives losing the email. Build a real parent portal when this module actually needs one of those properties.

## Key business rules to preserve regardless of phase

- An application cannot be submitted with required fields incomplete.
- A decision can only be made by an authorised role.
- An offer cannot exist without a prior decision.
- Enrolment cannot occur without required acceptance conditions met.
- Payment status cannot be manually altered by unauthorised users.
- A parent can only ever access their own family's records, no exceptions.
- Uploaded documents are never publicly accessible via predictable URLs; access requires an authenticated, permission-checked, signed request.

## Conceptual entities (target state)

```
Organisation → School → Campus → AcademicYear → Programme → YearGroup
Family → ParentGuardian, Student
Lead → Enquiry, Activity, Campaign, LeadSource
Application → ApplicationStage, ApplicationChecklist, ChecklistItem, ApplicationTask
ApplicationForm → FormSubmission, FormResponse
Document → DocumentRequirement, DocumentReview
Assessment → AssessmentType, AssessmentResult
Interview → InterviewSlot, InterviewParticipant, InterviewResult
Event → EventRegistration, Attendance
Review → ReviewStage, ReviewRubric, ReviewScore
AdmissionDecision → WaitlistEntry, Offer, OfferAcceptance, Enrolment
Invoice → Payment, PaymentAllocation
Communication → CommunicationTemplate, Notification
Workflow → WorkflowStep, WorkflowExecution
Tag → EntityTag
User → Role, Permission, DataAccessPolicy
AuditLog
AlumniRecord → AdvancementActivity
```

Only a small subset of this (see `02-stack-and-schema.md`) is built. The rest exists so later phases have somewhere to attach without a schema rewrite.

On the `Lead` line specifically: Phase 6 (2026-09-01) shipped a deliberately **flat** `Lead` model — `name`, `email`/`phone`, `grade_interest`, a `source` enum (quick-interest widget / PDF gate), marketing consent, unsubscribe token — with no separate `Enquiry`, `Activity`, `Campaign`, or `LeadSource` entities. `EmailCampaign` (Phase 6) can target a Lead audience, and `Lead.source` gives a coarse capture-point split, but full funnel/campaign/UTM attribution as sketched above is not built and is not currently scheduled. See `03-build-order.md`.

## UX principles (carry forward into every phase)

**Parent-facing:** every screen should answer, where am I? what have I completed? what do I still need to do? what happens next?

**Staff-facing:** prioritise, what requires my attention? what's overdue? what's blocking an application?

**Leadership-facing:** prioritise, how many applicants? how full are our classes? where are applicants coming from? what's our conversion rate?
