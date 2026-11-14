# CLAUDE.md — KareShafaa Agent Guidance

This file guides AI coding agents (Claude Code, Cursor, Codex, etc.) when working on the KareShafaa repository.

**Core rule: never change existing clinical, SLA, ownership, or patient-facing functionality without explicit tests that preserve behavior.**

## Project Overview

KareShafaa closes the screening-to-care loop for bowel (FIT), cervical (HPV), breast (mammogram/BI-RADS) and diabetes (HbA1c) programmes. Every actionable result becomes a tracked case with exactly one owner, an SLA clock (T0–T3), and an escalation path until documented closure.

Stack: FastAPI + SQLAlchemy + Jinja2 + SQLite/PostgreSQL. Synthetic data only. Not a certified medical device.

## ECC Principles Applied

We follow the ECC agent harness philosophy:

1. **Plan before building** — use planner agents for any multi-file change.
2. **Test-driven** — write or extend tests first, especially for clinical rules, state machine transitions, and SLA calculations.
3. **Security & privacy first** — patient data is synthetic but treated as PHI. No real identifiers, no unauthenticated access, audit everything.
4. **Review from fresh context** — after changes, review with a code-reviewer or security-reviewer agent.
5. **Remember** — capture decisions in docs/ or commit messages; avoid repeating mistakes.

## Healthcare-Specific Rules

- Clinical decisions stay with the treating clinician. The system only surfaces recommendations and tracks ownership.
- All dates/times are stored as naive UTC and displayed on the UAE clock (Asia/Dubai, UTC+4, no DST). See `kareshafaa/calendar_uae.py`.
- SLA tiers T0 (critical, 24/7) to T3 (routine, working days). Never weaken escalation for critical cases.
- Exactly one accountable owner per open case. Taking action claims unassigned cases.
- Closure requires evidence or an authorised human decision with reason.
- Teach-back and patient messaging respect preferred language (en/ar/hi/ur) and RTL.

## Recommended Agents (from ECC)

- `ecc:planner` for feature or refactor planning
- `ecc:tdd-guide` for clinical rules or state machine changes
- `ecc:code-reviewer` after every substantial change
- `ecc:security-reviewer` for any auth, patient data, or API change
- `ecc:architect` for structural decisions

## Skills to Use

- `healthcare-cdss-patterns` — pure-function clinical decision support
- `healthcare-phi-compliance` — access control, audit, data classification
- `python-testing`, `fastapi` patterns (adapt from ECC skills)
- Custom: `screening-to-care` (see skills/screening-to-care/SKILL.md)

## Running the Project

```bash
# Install
pip install -r requirements.txt -r requirements-dev.txt

# Run tests
pytest

# Dev server
uvicorn kareshafaa.main:app --reload
```

Never commit real credentials. Use `.env.example`.

## Safety

- Do not invent clinical thresholds. All rules live in `docs/clinical_rules.md` and `kareshafaa/clinical_rules.py`.
- Preserve the synthetic-only guarantee and the “not a medical device” disclaimer.
- Prefer additive changes. If behavior must change, update tests and document why.
