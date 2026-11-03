"""Explainability and governance views: "why this priority" for one case, rule cards for every clinical rule,
the register of human overrides, and the data-protection / retention statement.

Nothing here decides anything. It reads what the deterministic rules engine, the SLA clocks, the outreach model
and the people involved already recorded, and puts it into plain language."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clinical_rules as cr
from ..models import AuditEvent, Case, ModelVersion
from ..statemachine import LABELS, STATE_MACHINE_VERSION

RULES_DOC = Path(__file__).resolve().parents[2] / "docs" / "clinical_rules.md"
RULE_ID = re.compile(r"^[A-Z]{2,4}(-[A-Z0-9]+)+")
PROGRAMME_OF = {"CRC": "Bowel (FIT)", "CX": "Cervical (HPV / cytology)", "BR": "Breast (mammogram, BI-RADS)",
                "DM": "Diabetes (HbA1c / fasting glucose)", "GEN": "Any programme"}
TIER_WORDS = {"critical": "Critical (T0)", "urgent": "Urgent (T1)", "high": "Soon (T2)", "routine": "Routine (T3)"}
HUMAN_IN_LOOP = ("A person makes every clinical decision. The rules engine sets the urgency tier from the result; "
                 "only an authorised clinician can lower it, with a written reason. The outreach model only orders "
                 "cases inside the same tier for calling and can never change a tier, move a case or close it.")


def _clean(cell: str) -> str:
    return re.sub(r"\*\*|`", "", cell).strip()


@lru_cache(maxsize=1)
def rule_catalogue() -> dict[str, dict]:
    """Rule ID -> row of the rule tables in docs/clinical_rules.md (input, decision, tier, pathway, target, source).
    The document is the signed-off wording of the rules; the engine itself is kareshafaa/clinical_rules.py.
    Tables are read by their header names, so a table with a different column order still parses."""
    out: dict[str, dict] = {}
    try:
        text = RULES_DOC.read_text(encoding="utf-8")
    except OSError:
        return out
    section, header = "", None
    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
        if not line.startswith("|"):
            header = None
            continue
        if line.startswith("|---"):
            continue
        cells = [_clean(c) for c in line.strip().strip("|").split("|")]
        if header is None:
            header, labels = [h.lower() for h in cells], cells
            continue
        if "rule id" not in header or header[0] != "rule id" and len(cells) != len(header):
            continue
        if len(cells) < len(header):  # a merged input cell: keep the id, right-align the rest of the row
            tail = len(header) - 2
            k = len(cells) - 1 - (len(header) - 2 - 1)
            cells = [cells[0], "; ".join(cells[1:max(2, k + 1)])] + [""] * (tail - (len(cells) - max(2, k + 1))) + cells[max(2, k + 1):]
            header = header[:1] + ["input"] + header[2:] if len(cells) == len(header) else header
        col = dict(zip(header, cells))
        ids = [i.strip() for i in re.split(r"\s*/\s*|,\s*", col["rule id"]) if i.strip()]
        if not ids or not RULE_ID.match(ids[0].replace("*", "X")):
            continue
        full: list[str] = []
        prev = ids[0]
        for i in ids:
            if not RULE_ID.match(i.replace("*", "X")):  # "BR-4A/B/C" -> BR-4B, BR-4C; "CRC-FIT-QUAL-POS / NEG" -> ...-NEG
                i = prev[:-len(i)] + i if len(i) <= 2 else prev.rsplit("-", 1)[0] + "-" + i
            prev = i
            full += [i.replace("*", "A1C"), i.replace("*", "FPG")] if "*" in i else [i]
        known = {"rule id", "decision", "tier", "pathway", "target", "source"}
        known |= {"category"}
        parts = [(h, f"{lab}: {v}") for h, lab, v in zip(header, labels, cells) if h not in known and v not in ("", "–")]
        inp = col.get("input") or "; ".join(x for _, x in parts)
        row = {"input": inp, "decision": col.get("decision", col.get("category", "")), "tier": col.get("tier", ""),
               "pathway": col.get("pathway", ""), "target": col.get("target", ""), "source": col.get("source", ""),
               "section": section}
        for rid in full:
            r = {"id": rid, **row}
            if not col.get("input") and rid.startswith(("DM-A1C", "DM-FPG")):  # one test per rule
                key = "hba1c" if rid.startswith("DM-A1C") else "fpg"
                r["input"] = "; ".join(x for h, x in parts if key in h) or inp
            out.setdefault(rid, r)
    out.setdefault("GEN-ERR", {"id": "GEN-ERR", "input": "a test or programme the engine does not know", "decision": "review",
                               "tier": "urgent", "pathway": "human_review", "target": "review within the T1 clock",
                               "source": "fail-safe", "section": "1. Principles"})
    return out


def rule_cards() -> dict[str, Any]:
    cat = rule_catalogue()
    fam: dict[str, list] = {}
    for rid, r in cat.items():
        fam.setdefault(rid.split("-")[0], []).append(r)
    return {
        "engine": {"name": "KareShafaa clinical rules engine", "kind": "deterministic rules (no machine learning)",
                   "state_machine_version": STATE_MACHINE_VERSION, "rules_document": "docs/clinical_rules.md",
                   "status": "Draft for clinical-lead sign-off; items marked CONFIRM are local choices awaiting approval"},
        "purpose": "Decide whether a screening result needs action, how urgently (tier T0-T3), which pathway it follows "
                   "and the end-to-end target date, so no abnormal result is left without an owner and a deadline.",
        "inputs": ["test code and value as reported by the laboratory (HL7 v2 ORU^R01 or FHIR R4)", "unit as reported",
                   "result status (final, preliminary, amended)", "age and sex for programme eligibility",
                   "pregnancy, deceased and under-specialist-care flags"],
        "not_inputs": ["nationality", "language", "insurance", "the outreach model score"],
        "limits": ["Thresholds are taken from published guidelines (ADA, ASCCP, ACR BI-RADS, DoH Abu Dhabi programme "
                   "specifications) and some are local CONFIRM choices; they have not been validated on real UAE results.",
                   "Free-text or unusual results the engine cannot read with certainty go to a clinician (fail safe); they are never dropped.",
                   "Tested on synthetic data only."],
        "known_biases": ["Fixed thresholds do not adjust for ethnicity or comorbidity; e.g. HbA1c can read differently with "
                         "some haemoglobin variants and anaemia, which are more common in some populations in the region.",
                         "Age eligibility follows programme ranges; people outside them are routed to review rather than auto-actioned."],
        "human_override": "A clinician can raise the tier at any time. Lowering it is only possible when resolving a "
                          "clinical review, needs a written reason of at least 10 characters and is recorded in the "
                          "audit trail and the override register. A database interlock blocks any other downgrade.",
        "human_in_loop": HUMAN_IN_LOOP,
        "tier_targets_days": cr.TIER_TARGET_DAYS,
        "families": [{"code": k, "programme": PROGRAMME_OF.get(k, k), "rules": sorted(v, key=lambda r: r["id"])}
                     for k, v in sorted(fam.items())],
    }


def model_card_summary(db: Session) -> dict | None:
    m = db.scalar(select(ModelVersion).where(ModelVersion.status == "production").order_by(ModelVersion.version.desc()))
    if m is None:
        return None
    return {"version": m.version, "algorithm": m.algorithm, "status": m.status,
            "purpose": "Estimate the chance that a patient misses their next step, so coordinators call the most at-risk "
                       "people first within the same urgency tier.",
            "never": "Never sets or changes a tier, never moves a case between steps, never closes a case, never contacts a patient.",
            "monitored_not_used": ["gender", "nationality group", "age band", "language"],
            "approval": "A new version goes live only if it beats the current one on the same hold-out, passes the "
                        "calibration and fairness gates, and a clinical lead approves it. Rollback is one click."}


def why_priority(db: Session, c: Case, sla_hours: float | None = None) -> dict[str, Any]:
    rule = rule_catalogue().get(c.rule_id or "") if c.rule_id else None
    if rule:  # on the case screen a CONFIRM marker reads as plain words; the rule card keeps the marker
        rule = {**rule, "source": re.sub(r"\bCONFIRM\b", "local choice awaiting sign-off", rule["source"])}
    reasons = list(c.review_reasons or [])
    log = list(c.review_log or [])
    overrides = [{"at": e.get("at"), "by": e.get("by"), "note": e.get("note"), "tier_from": e.get("tier_from"),
                  "tier_to": e.get("tier_to"),
                  "kind": "lowered" if cr.is_downgrade(e.get("tier_from"), e.get("tier_to")) else
                          ("raised" if e.get("tier_from") != e.get("tier_to") else "confirmed")} for e in log]
    escalated = db.execute(select(AuditEvent.ts, AuditEvent.actor_display, AuditEvent.detail)
                           .where(AuditEvent.case_id == c.id, AuditEvent.event == "case.tier_escalated")
                           .order_by(AuditEvent.id)).all()
    steps = []
    if rule:
        steps.append(f"Rule {c.rule_id}: {rule['input']} → {rule['decision']}.")
    elif c.rule_id:
        steps.append(f"Rule {c.rule_id} set the starting tier.")
    for ts, who, d in escalated:
        d = d or {}
        steps.append(f"Raised from {TIER_WORDS.get(d.get('from'), d.get('from'))} to {TIER_WORDS.get(d.get('to'), d.get('to'))}"
                     f" because: {', '.join(r.replace('_', ' ') for r in d.get('reasons') or []) or 'safety rule'}.")
    if reasons and not escalated:
        steps.append(f"Flagged for a clinician's review because: {', '.join(r.replace('_', ' ') for r in reasons)}; "
                     + (f"handled as {TIER_WORDS.get(c.tier, c.tier)} until a clinician decides." if c.review_required
                      else f"handled as {TIER_WORDS.get(c.tier, c.tier)} while a clinician reviewed it."))
    if not overrides and reasons:  # older records: the decision is only in the audit trail
        for ts, who, d in db.execute(select(AuditEvent.ts, AuditEvent.actor_display, AuditEvent.detail)
                                     .where(AuditEvent.case_id == c.id, AuditEvent.event == "case.review_resolved")
                                     .order_by(AuditEvent.id)).all():
            d = d or {}
            overrides.append({"at": ts.isoformat(), "by": who, "note": d.get("note"), "tier_from": d.get("tier_from"),
                              "tier_to": d.get("tier_to"),
                              "kind": "lowered" if d.get("downgrade") else ("raised" if d.get("tier_from") != d.get("tier_to") else "confirmed")})
    for o in overrides:
        if o["kind"] != "confirmed":
            steps.append(f"{o['by']} {o['kind']} the tier to {TIER_WORDS.get(o['tier_to'], o['tier_to'])}: “{(o['note'] or '').rstrip('.')}”.")
        else:
            steps.append(f"{o['by']} reviewed it and kept {TIER_WORDS.get(o['tier_to'], o['tier_to'])}"
                         + (f": “{o['note'].rstrip('.')}”." if o.get("note") else "."))
    return {
        "tier": c.tier, "tier_label": TIER_WORDS.get(c.tier, c.tier),
        "decided_by": ("rules engine, then a clinician" if any(o["kind"] != "confirmed" for o in overrides)
                       else "rules engine, confirmed by a clinician" if overrides else "rules engine"),
        "rule": rule or ({"id": c.rule_id} if c.rule_id else None),
        "pathway": c.pathway, "target_text": c.target_text,
        "target_due_at": c.target_due_at.isoformat() if c.target_due_at else None,
        "step": {"state": c.state, "label": LABELS.get(c.state, c.state), "due_at": c.due_at.isoformat() if c.due_at else None,
                 "sla_hours": sla_hours},
        "review": {"required": bool(c.review_required), "reasons": reasons},
        "overrides": overrides, "steps": steps,
        "ml": {"score": None if c.risk_score is None else round(c.risk_score, 3), "model_version": c.risk_model_version,
               "role": "Orders outreach within this tier only. It did not set the tier."},
        "human_in_loop": HUMAN_IN_LOOP,
    }


OVERRIDE_EVENTS = {
    "case.review_resolved": "Clinical review resolved", "case.closed": "Closed by clinician decision",
    "case.reopened": "Case reopened", "model.approved": "Model approved", "model.rejected": "Model rejected",
    "model.rolled_back": "Model rolled back", "proposal.accepted": "Rule change accepted",
    "proposal.rejected": "Rule change rejected", "patient.duplicate_override": "Possible duplicate registered anyway",
    "referral.rejected": "Referral rejected by specialist",
}


def override_register(db: Session, limit: int = 200, kind: str | None = None) -> dict[str, Any]:
    events = [kind] if kind in OVERRIDE_EVENTS else list(OVERRIDE_EVENTS)
    rows = db.execute(select(AuditEvent).where(AuditEvent.event.in_(events), AuditEvent.actor_role.not_in(("system", "ml_service")))
                      .order_by(AuditEvent.ts.desc(), AuditEvent.id.desc()).limit(limit * 3)).scalars().all()
    items = []
    counts: dict[str, int] = {}
    for e in rows:
        d = e.detail or {}
        if e.event == "case.closed" and d.get("closure_type") != "authorised_decision":
            continue
        what, why = "", d.get("note") or d.get("reason") or d.get("rationale") or ""
        if e.event == "case.review_resolved":
            lowered = bool(d.get("downgrade"))
            what = (f"Tier {'lowered' if lowered else 'kept' if d.get('tier_from') == d.get('tier_to') else 'raised'}: "
                    f"{TIER_WORDS.get(d.get('tier_from'), d.get('tier_from'))} → {TIER_WORDS.get(d.get('tier_to'), d.get('tier_to'))}")
        elif e.event == "case.closed":
            what = f"Closed without care evidence ({str(d.get('reason') or '').replace('_', ' ')})"
        items.append({"id": e.id, "at": e.ts.isoformat(), "event": e.event, "label": OVERRIDE_EVENTS[e.event],
                      "by": e.actor_display, "role": e.actor_role, "case_id": e.case_id, "what": what,
                      "reason": str(why)[:300], "lowered_tier": bool(d.get("downgrade"))})
        if len(items) >= limit:
            break
    # totals over the whole audit log (not just the rows listed)
    human = AuditEvent.actor_role.not_in(("system", "ml_service"))
    counts = {ev: n for ev, n in db.execute(select(AuditEvent.event, func.count(AuditEvent.id))
                                            .where(AuditEvent.event.in_([x for x in events if x != "case.closed"]), human)
                                            .group_by(AuditEvent.event)).all()}
    if "case.closed" in events:
        counts["case.closed"] = sum(1 for (d,) in db.execute(select(AuditEvent.detail).where(AuditEvent.event == "case.closed", human))
                                    if (d or {}).get("closure_type") == "authorised_decision")
    lowered = sum(1 for (d,) in db.execute(select(AuditEvent.detail).where(AuditEvent.event == "case.review_resolved", human))
                  if (d or {}).get("downgrade")) if "case.review_resolved" in events else 0
    return {"items": items, "counts": {k: counts.get(k, 0) for k in events}, "lowered_tiers": lowered, "listed": len(items), "labels": OVERRIDE_EVENTS,
            "note": "Every place where a person accepted, changed or overruled what the rules engine, the model or the "
                    "proposal engine suggested. Each entry is also in the hash-chained audit trail."}


# ---- data protection: how the prototype maps to UAE / Abu Dhabi rules (sources checked 3 Nov 2026) -------------
DATA_PROTECTION = [
    {"framework": "UAE Federal Law No. 2 of 2019 on the use of ICT in health fields",
     "url": "https://elaws.moj.gov.ae/UAE-MOJ_LC-En/00_Health/UAE-LC-En_2019-02-06_00002_Kait.html?val=EL1",
     "asks": "Keep health data confidential, accurate and available to authorised people (Art. 4); keep health data "
             "inside the UAE unless the health authority allows otherwise (Art. 13); keep it at least 25 years after "
             "the last health procedure (Art. 20).",
     "prototype": "Role-based access on every request, append-only hash-chained audit, no deletion of clinical data, "
                  "retention settings below. Synthetic records only.",
     "pilot": "Host every component in a UAE region or on premises (the free prototype hosting is outside the UAE and "
              "must never hold real patient data).", "status": "partly"},
    {"framework": "UAE Federal Decree-Law No. 45 of 2021 on personal data protection (PDPL)",
     "url": "https://www.uaelegislation.gov.ae/en/legislations/1972/download",
     "asks": "Applies to personal data processing in the UAE. Art. 2(2)(e) excludes personal health data that has its "
             "own legislation (above); staff and other non-health personal data stay in scope. Art. 4 allows processing "
             "without consent when needed for diagnosis, care and managing health services.",
     "prototype": "Staff data is limited to name, role, site and contact; passwords are argon2id hashes; patients see "
                  "and correct their own contact details; contact consent and analytics opt-out are recorded and honoured.",
     "pilot": "Data processing agreement with the provider; records of processing; breach notification runbook.", "status": "partly"},
    {"framework": "DoH Abu Dhabi Healthcare Information and Cyber Security Standard (ADHICS) V2, 2024",
     "url": "https://www.doh.gov.ae/-/media/Feature/Resources/Standards/ADHICS-v2-standard.ashx",
     "asks": "Information security governance and controls for any entity in Abu Dhabi that handles health "
             "information, incl. access control, audit logging, privacy and third-party systems. It supersedes the "
             "2020 Standard on Patient Healthcare Data Privacy.",
     "prototype": "Individual accounts, lockout, session timeout, CSRF protection, least-privilege roles (the "
                  "administrator has no clinical access), audit of reads and writes, tamper-evident audit chain.",
     "pilot": "Formal ADHICS gap assessment and the Malaffi security assessment before connecting.", "status": "partly"},
    {"framework": "DoH Abu Dhabi Health Information Exchange data standards (Malaffi)",
     "url": "https://www.doh.gov.ae/en/resources/HIE",
     "asks": "Mandated coding standards, units and a minimum data set for data sent to Malaffi; demographic standards "
             "include Emirates ID and passport, name, date of birth, nationality, phone and email.",
     "prototype": "Registration captures these fields with the same rules (Emirates ID check digit, passport, "
                  "nationality, E.164 phone); results arrive as HL7 v2 ORU^R01 or FHIR R4.",
     "pilot": "Map local codes to the DoH code sets and pass Malaffi onboarding.", "status": "partly"},
    {"framework": "DoH Abu Dhabi Responsible AI Standard (2025) and Policy on Use of AI in the Healthcare Sector",
     "url": "https://www.doh.gov.ae/-/media/Feature/Resources/Standards/2025/Responsible-AI-Standard-V1.ashx",
     "asks": "Human oversight, transparency about the role of AI, validation, monitoring, graceful degradation.",
     "prototype": "Rules decide; the model only orders outreach within a tier; model cards, fairness and calibration "
                  "gates, clinical-lead approval, rollback, drift monitoring; this page and the case screen explain every priority.",
     "pilot": "AI risk register with named owners; incident reporting.", "status": "partly"},
]


def data_protection(db: Session) -> dict:
    from ..config import get_settings
    from .retention import SETTINGS

    s = get_settings()
    url = s.database_url or ""
    engine = "PostgreSQL" if url.startswith(("postgres", "postgresql")) else "SQLite"
    return {"frameworks": DATA_PROTECTION, "retention": SETTINGS,
            "hosting": {"database": engine, "mode": s.mode,
                        "residency": "This prototype runs on free cloud hosting outside the UAE with synthetic records "
                                     "only. A pilot must run in a UAE region or on premises."},
            "checked": "2026-11-03",
            "disclaimer": "A mapping prepared by the product team, not a legal opinion or a certification."}
