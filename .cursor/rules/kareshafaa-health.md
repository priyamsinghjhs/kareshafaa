---
description: "KareShafaa clinical and safety invariants"
globs: ["kareshafaa/**/*.py", "tests/**/*.py", "docs/clinical*.md"]
alwaysApply: true
---
# KareShafaa Clinical & Safety Rules

These rules extend the Python and common rules. They are non-negotiable for this repository.

## Clinical Integrity

- Never invent or hard-code clinical thresholds. Source them from `docs/clinical_rules.md` and keep `kareshafaa/clinical_rules.py` in sync.
- Unknown, conflicting, or out-of-range results must go to the clinical review queue at a safe tier.
- Critical (T0) results require documented clinician read-back before triage.
- Do not allow a critical case to be closed as `unable_to_contact`.

## Ownership & SLA

- Every open case has exactly one owner. Unassigned cases are claimed by the first staff member who takes an action.
- SLA clocks respect tier: T0/T1 run 24/7; T2/T3 use UAE working days.
- Booked appointments are not overdue until the end of their UAE calendar day.

## Patient Data & Privacy

- All data is synthetic. Never introduce real patient identifiers.
- Audit every PHI access or modification (existing audit trail must be preserved).
- Preferred language and RTL must be respected in patient-facing content.
- Teach-back answers must match the pathway text in the selected language.

## Time Handling

- Store all clinical datetimes as naive UTC.
- Display and accept input on the UAE clock (Asia/Dubai, UTC+4, no DST).
- Calendar-date fields (DOB, medicine start/end) never shift a day.

## Testing

- Any change to state machine, SLA, ownership, or clinical rules must have a corresponding test that would fail if the invariant is broken.
- Prefer pure functions for clinical logic so they remain fully unit-testable.
