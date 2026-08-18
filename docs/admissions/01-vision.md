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

Only a small subset of this (see `02-stack-and-schema.md`) is built in Phase 1. The rest exists so later phases have somewhere to attach without a schema rewrite.

## UX principles (carry forward into every phase)

**Parent-facing:** every screen should answer, where am I? what have I completed? what do I still need to do? what happens next?

**Staff-facing:** prioritise, what requires my attention? what's overdue? what's blocking an application?

**Leadership-facing:** prioritise, how many applicants? how full are our classes? where are applicants coming from? what's our conversion rate?
