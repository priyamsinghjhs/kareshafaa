"""TEST ORACLE ONLY: proposal generation as it was before the batched lookups (one pending-proposal query per
candidate, configuration read per language). Used by tests/test_proposals_batch.py to prove identical proposals."""
from __future__ import annotations

import json
from collections import Counter, defaultdict

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from kareshafaa import audit
from kareshafaa.models import Communication, Lesson, Organization, Patient, QuestionnaireResponse, RuleProposal, StepTask, Case, utcnow
from kareshafaa.security import Actor
from kareshafaa.services.configstore import get_config, DEFAULT_CHANNEL_ORDER
from kareshafaa.services.errors import Forbidden
from kareshafaa.services.improve import BARRIER_ACTIONS, MIN_N
from kareshafaa.sla import SlaLookup


def channel_order(db, language):
    return list(get_config(db, f"channel_order:{language}", None) or get_config(db, "channel_order:default",
                                                                                 DEFAULT_CHANNEL_ORDER))


def template_variant(db, category, language):
    return str(get_config(db, f"template_variant:{category}:{language}", "standard"))


def _fingerprint(kind: str, target: dict) -> str:
    return kind + ":" + json.dumps(target, sort_keys=True)


def _propose(db: Session, out: list, kind: str, target: dict, current, proposed, rationale: str, evidence: dict) -> None:
    fp = _fingerprint(kind, target)
    if db.scalar(select(RuleProposal).where(RuleProposal.fingerprint == fp, RuleProposal.status == "pending")):
        return
    p = RuleProposal(kind=kind, target=target, current_value={"value": current}, proposed_value={"value": proposed},
                     rationale=rationale, evidence=evidence, fingerprint=fp, created_at=utcnow())
    db.add(p)
    db.flush()
    out.append(p)


def generate_proposals_old(db: Session, actor: Actor) -> list[RuleProposal]:
    if not actor.can("proposal:generate"):
        raise Forbidden("role lacks proposal:generate")
    out: list[RuleProposal] = []
    sla = SlaLookup.load(db)
    # 1) SLA performance per (state, tier) from completed steps
    data: dict[tuple[str, str], list[tuple[float, bool]]] = defaultdict(list)
    for st, tier, start, end, breached in db.execute(
            select(StepTask.state, Case.tier, StepTask.started_at, StepTask.ended_at, StepTask.breached)
            .join(Case, Case.id == StepTask.case_id).where(StepTask.status == "completed", StepTask.ended_at.is_not(None),
                                                           StepTask.state != "closed")):
        data[(st, tier)].append(((end - start).total_seconds() / 3600, bool(breached)))
    for (st, tier), vals in sorted(data.items()):
        hours = sla.hours(st, tier)
        if hours is None or len(vals) < MIN_N:
            continue
        durs = np.array([v[0] for v in vals])
        breach = float(np.mean([v[1] for v in vals]))
        p90 = float(np.percentile(durs, 90))
        ev = {"n": len(vals), "breach_rate": round(breach, 3), "p50_hours": round(float(np.median(durs)), 1),
              "p90_hours": round(p90, 1), "sla_hours": hours}
        rf = sla.reminder_fraction(st, tier)
        if breach >= 0.30 and rf > 0.3:
            _propose(db, out, "reminder_fraction", {"state": st, "tier": tier}, rf, round(max(0.25, rf - 0.15), 2),
                     f"{breach:.0%} of '{st}' steps ({tier}) breached their SLA. Send the L1 reminder earlier so owners "
                     "act before breach. (Relaxing the SLA is not proposed automatically.)", ev)
        if breach <= 0.05 and len(vals) >= 30 and p90 <= 0.5 * hours:
            new = float(round(max(p90 * 1.25, hours * 0.5)))
            _propose(db, out, "sla_hours", {"state": st, "tier": tier}, hours, new,
                     f"'{st}' ({tier}) is consistently completed well within its SLA (p90 {p90:.0f}h vs {hours:.0f}h). "
                     "A tighter target would surface slow cases sooner.", ev)
    # 2) teach-back failure by language -> simplified template variant
    tb: dict[str, list[bool]] = defaultdict(list)
    for lang, passed in db.execute(select(QuestionnaireResponse.language, QuestionnaireResponse.passed)
                                   .where(QuestionnaireResponse.mode == "self")):
        tb[lang].append(bool(passed))
    for lang, vals in tb.items():
        if len(vals) < 15:
            continue
        fail = 1 - float(np.mean(vals))
        cur = template_variant(db, "result_notification", lang)
        if fail >= 0.25 and cur != "simplified":
            _propose(db, out, "template_variant", {"category": "result_notification", "language": lang}, cur, "simplified",
                     f"{fail:.0%} of patients reading '{lang}' messages failed the teach-back check. Switch to the shorter "
                     "'simplified' wording (requires translator and clinical review before go-live).",
                     {"n": len(vals), "fail_rate": round(fail, 3)})
    # 3) channel effectiveness by language -> reorder ladder
    ch: dict[tuple[str, str], list[bool]] = defaultdict(list)
    for lang, chan, status in db.execute(select(Patient.language, Communication.channel, Communication.status)
                                         .join(Patient, Patient.id == Communication.patient_id)
                                         .where(Communication.channel.in_(["app", "sms", "whatsapp"]),
                                                Communication.status.in_(["completed", "failed"]))):
        ch[(lang, chan)].append(status == "completed")
    for lang in sorted({k[0] for k in ch}):
        rates = {c: (float(np.mean(v)), len(v)) for (lg, c), v in ch.items() if lg == lang and len(v) >= MIN_N}
        order = channel_order(db, lang)
        first = order[0]
        if first in rates and "whatsapp" in rates and first != "whatsapp":
            if rates[first][0] < 0.5 and rates["whatsapp"][0] >= rates[first][0] + 0.2:
                new = ["whatsapp"] + [c for c in order if c != "whatsapp"]
                _propose(db, out, "channel_order", {"language": lang}, order, new,
                         f"For '{lang}' speakers the first channel '{first}' delivered only {rates[first][0]:.0%}; WhatsApp "
                         f"delivered {rates['whatsapp'][0]:.0%}. Try WhatsApp first (consent still checked per channel).",
                         {k: {"delivery_rate": round(v[0], 3), "n": v[1]} for k, v in rates.items()})
    # 4) referral rejections for incomplete information -> mandatory checklist
    rej = Counter(db.scalars(select(Lesson.target_org_id).where(Lesson.kind == "rejected_referral",
                                                                Lesson.reason_code == "incomplete_info")))
    for org_id, n in rej.items():
        if org_id and n >= 5:
            org = db.get(Organization, org_id)
            _propose(db, out, "referral_checklist", {"org_id": org_id}, None, {"min_summary_chars": 40},
                     f"{n} referrals to {org.name if org else org_id} were rejected for incomplete information. Require a "
                     "structured clinical summary (>= 40 characters) before a referral can be sent.", {"n": n})
    # 5) no-show barriers by region -> advisory (human action, nothing auto-applied)
    ns = Counter(db.execute(select(Lesson.region, Lesson.reason_code).where(Lesson.kind == "no_show")).all())
    by_region: dict[str, int] = defaultdict(int)
    for (region, _), n in ns.items():
        by_region[region] += n
    for (region, reason), n in ns.items():
        if by_region[region] >= 15 and n / by_region[region] >= 0.25 and reason not in ("unknown", "forgot"):
            _propose(db, out, "advisory", {"region": region, "barrier": reason}, None,
                     f"Offer targeted support in {region}: {BARRIER_ACTIONS.get(reason, 'review barrier with patients')}.",
                     f"{n}/{by_region[region]} no-shows in {region} cite '{reason}'.", {"n": n, "region_total": by_region[region]})
    # 6) teach-back failures among patients whose language has no template -> advisory
    from kareshafaa.comms.content import SUPPORTED_LANGUAGES

    tbl = Counter(db.scalars(select(Lesson.language).where(Lesson.kind == "teach_back_failed")))
    for lang, n in tbl.items():
        if lang and lang not in SUPPORTED_LANGUAGES and n >= 10:
            _propose(db, out, "advisory", {"language": lang, "topic": "language_coverage"}, None,
                     f"Add professionally translated '{lang}' templates, or route '{lang}' speakers to an interpreter-led call first.",
                     f"{n} teach-back failures among '{lang}' speakers, who currently receive English messages.", {"n": n})
    for p in out:
        audit.record(db, actor, "C", "proposal.created", entity_type="RuleProposal", entity_id=p.id,
                     detail={"kind": p.kind, "target": p.target})
    return out


