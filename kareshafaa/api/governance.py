"""Governance API: model registry (train / approve / reject / rollback / drift / model card),
improvement proposals (generate / decide), SLA rules and active configuration."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..comms import governance as content_gov
from ..config import get_settings
from ..db import get_db
from ..ml import registry
from ..models import ModelVersion, RuleProposal, SlaRule
from ..security import Actor
from ..services.configstore import all_active
from ..services.improve import decide_proposal, generate_proposals
from . import schemas as sch
from .deps import require

router = APIRouter(prefix="/api/governance", tags=["governance"])


def _suppress_fairness(f: dict) -> dict:
    thr = get_settings().suppression_threshold
    out = {}
    for attr, v in f.items():
        groups = []
        for g in v["groups"]:
            g = dict(g)
            if g["n"] < thr:
                g.update(n=f"<{thr}", n_pos="suppressed", observed_rate="suppressed", mean_pred="suppressed",
                         calibration_diff="suppressed")
            elif g.get("n_pos", 0) < thr:
                g.update(n_pos=f"<{thr}")
            groups.append(g)
        out[attr] = {**v, "groups": groups}
    return out


def _mv(m: ModelVersion, full: bool = False) -> dict:
    d = {"version": m.version, "algorithm": m.algorithm, "status": m.status, "created_at": m.created_at.isoformat(),
         "trained_on_n": m.trained_on_n, "validated_on_n": m.validated_on_n,
         "metrics": {k: v for k, v in m.metrics.items() if full or k != "calibration_curve"},
         "gates_passed": m.gates.get("all_passed"), "approved_at": m.approved_at.isoformat() if m.approved_at else None,
         "approved_by_user_id": m.approved_by_user_id, "decision_note": m.decision_note,
         "compared_to_version": m.compared_to_version, "artifact_sha256": m.artifact_sha256}
    if full:
        d.update(gates=m.gates, fairness=_suppress_fairness(m.fairness), features=m.features)
    return d


@router.get("/models")
def models(actor: Actor = Depends(require("model:read")), db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(ModelVersion).order_by(ModelVersion.version.desc())).all()
    prod = registry.production(db)
    return {"production_version": prod.version if prod else None, "items": [_mv(m) for m in rows],
            "use_statement": "Outreach prioritisation and queue ordering ONLY. Never used for clinical decisions, "
                             "state changes or closure."}


@router.get("/models/{version}")
def model_detail(version: int, actor: Actor = Depends(require("model:read")), db: Session = Depends(get_db)) -> dict:
    return _mv(registry.get_version(db, version), full=True)


@router.get("/models/{version}/card", response_class=PlainTextResponse)
def model_card(version: int, actor: Actor = Depends(require("model:read")), db: Session = Depends(get_db)) -> str:
    return registry.get_version(db, version).model_card


@router.post("/models/train")
def train(body: sch.TrainIn, actor: Actor = Depends(require("model:train")), db: Session = Depends(get_db)) -> dict:
    mv = registry.train_candidate(db, actor, body.algorithm)
    db.commit()
    return _mv(mv, full=True)


@router.post("/models/{version}/approve")
def approve(version: int, body: sch.ApprovalIn, actor: Actor = Depends(require("model:approve")),
            db: Session = Depends(get_db)) -> dict:
    mv = registry.approve(db, version, actor, body.note)
    db.commit()
    return _mv(mv)


@router.post("/models/{version}/reject")
def reject(version: int, body: sch.ApprovalIn, actor: Actor = Depends(require("model:approve")),
           db: Session = Depends(get_db)) -> dict:
    mv = registry.reject(db, version, actor, body.note)
    db.commit()
    return _mv(mv)


@router.post("/models/rollback")
def rollback(body: sch.ApprovalIn, actor: Actor = Depends(require("model:rollback")), db: Session = Depends(get_db)) -> dict:
    mv = registry.rollback(db, actor, body.note)
    db.commit()
    return {"ok": True, "production_version": mv.version}


@router.get("/drift")
def drift(actor: Actor = Depends(require("model:read")), db: Session = Depends(get_db)) -> dict:
    return registry.drift_report(db)


@router.get("/proposals")
def proposals(status: str | None = None, actor: Actor = Depends(require("lesson:read")), db: Session = Depends(get_db)) -> dict:
    q = select(RuleProposal).order_by(RuleProposal.status.desc(), RuleProposal.id.desc())
    if status:
        q = q.where(RuleProposal.status == status)
    return {"items": [{"id": p.id, "kind": p.kind, "target": p.target, "current": p.current_value.get("value") if p.current_value else None,
                       "proposed": p.proposed_value.get("value"), "rationale": p.rationale, "evidence": p.evidence,
                       "status": p.status, "decision_note": p.decision_note,
                       "decided_at": p.decided_at.isoformat() if p.decided_at else None,
                       "created_at": p.created_at.isoformat()} for p in db.scalars(q)]}


@router.post("/proposals/generate")
def proposals_generate(actor: Actor = Depends(require("proposal:generate")), db: Session = Depends(get_db)) -> dict:
    out = generate_proposals(db, actor)
    db.commit()
    return {"ok": True, "created": len(out)}


@router.post("/proposals/{proposal_id}/decide")
def proposals_decide(proposal_id: int, body: sch.ProposalDecisionIn, actor: Actor = Depends(require("proposal:decide")),
                     db: Session = Depends(get_db)) -> dict:
    p = decide_proposal(db, proposal_id, actor, body.approve, body.note)
    db.commit()
    return {"ok": True, "status": p.status}


@router.get("/sla-rules")
def sla_rules(actor: Actor = Depends(require("lesson:read")), db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(SlaRule).where(SlaRule.active.is_(True)).order_by(SlaRule.state, SlaRule.tier)).all()
    return {"items": [{"state": r.state, "tier": r.tier, "hours": r.hours, "reminder_fraction": r.reminder_fraction,
                       "version": r.version, "source": r.source} for r in rows]}


@router.get("/config")
def config(actor: Actor = Depends(require("lesson:read")), db: Session = Depends(get_db)) -> dict:
    return {"items": [{"key": c.key, "value": c.value, "version": c.version, "source": c.source,
                       "updated_at": c.updated_at.isoformat()} for c in all_active(db)]}


@router.get("/content")
def content_status(actor: Actor = Depends(require("content:read")), db: Session = Depends(get_db)) -> dict:
    rows = content_gov.status(db)
    return {"items": rows, "summary": {"items": len(rows), "readability_ok": sum(r["readability"]["ok"] for r in rows),
                                       "gated": sum(r["gated"] for r in rows),
                                       "gated_live": sum(r["gated"] and r["live"] for r in rows)}}


@router.post("/content/signoff")
def content_signoff(body: sch.ContentSignoffIn, actor: Actor = Depends(require("content:read")),
                    db: Session = Depends(get_db)) -> dict:
    a = content_gov.approve(db, actor, body.content_id, body.kind, body.note)
    db.commit()
    return {"id": a.id, "content_id": a.content_id, "kind": a.kind, "by": a.display, "sha256": a.sha256[:12]}


# ---- explainability and data governance views ---------------------------------------------------------------
@router.get("/rule-cards")
def rule_cards(actor: Actor = Depends(require("model:read")), db: Session = Depends(get_db)) -> dict:
    """Rule card (purpose, inputs, limits, known biases, human override, version) for the deterministic rules
    engine, every rule with its source, and a summary of the outreach model card."""
    from ..services.explain import model_card_summary, rule_cards as cards

    return {**cards(), "model": model_card_summary(db)}


@router.get("/overrides")
def overrides(kind: str | None = None, limit: int = 200, actor: Actor = Depends(require("audit:read")),
              db: Session = Depends(get_db)) -> dict:
    """Register of human overrides: reviews resolved (incl. lowered tiers, with the reason), closures by clinician
    decision, model and rule-change decisions."""
    from ..services.explain import override_register

    return override_register(db, limit=max(1, min(limit, 500)), kind=kind)


@router.get("/data-protection")
def data_protection(actor: Actor = Depends(require("model:read")), db: Session = Depends(get_db)) -> dict:
    from ..services.explain import data_protection as dp

    return dp(db)
