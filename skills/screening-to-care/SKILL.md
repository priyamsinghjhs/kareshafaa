---
name: screening-to-care
description: Patterns for the KareShafaa screening-to-care loop. Use when implementing or modifying case ownership, SLA clocks, escalation, teach-back, patient notifications, or clinical result interpretation for FIT, HPV, BI-RADS or HbA1c.
metadata:
  version: "1.0.0"
  origin: Adapted from ECC healthcare skills + KareShafaa domain
---

# Screening-to-Care Loop Patterns

KareShafaa turns every actionable screening result into an owned, timed case that stays visible until closed with evidence.

## When to Use

- Adding or changing a pathway (bowel FIT, cervical HPV, breast BI-RADS, diabetes HbA1c)
- Modifying the state machine, ownership rules, or SLA tiers
- Implementing patient teach-back or multi-language messaging
- Handling critical results (T0) that require direct clinician read-back
- Working on appointment booking, no-show, or referral rejection flows

## Core Invariants (do not break)

1. **Exactly one accountable owner** for every open case (enforced in DB + service layer).
2. **SLA tiers T0–T3**:
   - T0 critical: 24/7 clock, phone escalation from first breach, cannot close as “unable to contact”.
   - T1 urgent, T2 soon, T3 routine: UAE working-day clocks where applicable.
3. **Critical read-back**: a critical result cannot be triaged until a clinician records direct contact + read-back.
4. **Booked appointment wait**: due at end of the appointment’s UAE day; no escalation before then.
5. **Closure guard**: evidence (care plan, procedure, negative confirmation) or authorised human decision + reason.
6. **Language fidelity**: teach-back correct answer and patient pathway text use the selected language (en/ar/hi/ur), not English source.
7. **UAE clock everywhere a human sees a time**: stored naive UTC, displayed Asia/Dubai.

## Recommended Workflow (ECC-style)

1. Read the relevant section in `docs/clinical_rules.md` and the corresponding function in `kareshafaa/clinical_rules.py`.
2. Write or extend a failing test in `tests/` that captures the desired invariant.
3. Implement the minimal change.
4. Run `pytest` and the specific test file.
5. Review with a fresh security/code-review agent focusing on ownership, audit, and PHI.

## Key Files

- `kareshafaa/statemachine.py` — case states and transitions
- `kareshafaa/sla.py` / `alert_policy.py` — clocks and escalation
- `kareshafaa/clinical_rules.py` — result interpretation
- `kareshafaa/calendar_uae.py` — UAE time handling
- `docs/clinical_rules.md` — source of clinical truth
- `docs/clinical_safety_case.md` — safety arguments

## Testing Notes

- Synthetic patients only (`SYN-` prefix).
- Time-dependent tests use freezegun or explicit UTC inputs.
- Role-based queue filtering and claim-on-action must stay consistent with `available_actions`.
