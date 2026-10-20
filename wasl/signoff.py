"""Registry of HIGH-IMPACT actions and the single guard that enforces explicit human sign-off on each.

Every action below changes what happens to a patient, who is accountable, what the system will do automatically,
or what patients are told. Each one must be taken by a NAMED HUMAN (is_human and a user id - never the rules
engine, the sweep, the ML service or an import job), holding the listed permission, with a written rationale
where shown, and is recorded in the hash-chained audit trail by the service function that performs it.

`tests/test_human_signoff.py` checks that every listed function calls `require_signoff` with its action name,
that automated actors are refused even when they carry a privileged role, and that the rationale is enforced.
"""
from __future__ import annotations

from dataclasses import dataclass

from .security import Actor
from .services.errors import Forbidden, Invalid


@dataclass(frozen=True)
class HighImpact:
    perms: tuple[str, ...]  # any one of these
    min_note: int  # minimum rationale length (0 = no free-text rationale required by this guard)
    where: str  # module:function that performs it (checked by tests)
    why: str


HIGH_IMPACT: dict[str, HighImpact] = {
    "case.close.authorised": HighImpact(("closure:authorise",), 10, "wasl.services.workflow:close_case",
                                        "Ends follow-up without clinical evidence (decline, unable to contact, moved...)."),
    "case.close.evidence": HighImpact(("case:write",), 0, "wasl.services.workflow:close_case",
                                      "Ends follow-up on recorded evidence; evidence is verified against the case."),
    "case.reopen": HighImpact(("case:reopen",), 10, "wasl.services.workflow:transition",
                              "Restarts a closed pathway and its clocks."),
    "case.review.resolve": HighImpact(("case:triage_urgent",), 10, "wasl.services.workflow:resolve_review",
                                      "Clears a fail-safe clinical review flag; may LOWER the urgency tier."),
    "case.assign": HighImpact(("case:assign",), 3, "wasl.services.workflow:assign",
                              "Changes the accountable owner."),
    "case.transfer.request": HighImpact(("case:transfer",), 5, "wasl.services.workflow:request_transfer",
                                        "Starts a two-key hand-over of accountability to another organisation."),
    "case.transfer.decide": HighImpact(("case:read",), 0, "wasl.services.workflow:decide_transfer",
                                       "Accepts or declines accountability (a decline needs a reason)."),
    "confirmatory.result": HighImpact(("confirm:result",), 0, "wasl.services.workflow:record_confirmatory_result",
                                      "A negative confirmatory result moves the case towards closure/surveillance."),
    "referral.decide": HighImpact(("referral:decide",), 0, "wasl.services.workflow:decide_referral",
                                  "Accepting or rejecting a referral (rejection needs a reason code and note)."),
    "careplan.start": HighImpact(("careplan:write",), 5, "wasl.services.workflow:start_care",
                                 "Records that treatment or surveillance started: stops the pathway target clock."),
    "escalation.ack": HighImpact(("escalation:ack",), 3, "wasl.services.escalation:acknowledge_escalation",
                                 "Acknowledges an SLA / pathway-target alert."),
    "model.approve": HighImpact(("model:approve",), 5, "wasl.ml.registry:approve",
                                "Puts a new outreach-priority model into production."),
    "model.reject": HighImpact(("model:approve",), 5, "wasl.ml.registry:reject", "Rejects a candidate model."),
    "model.rollback": HighImpact(("model:rollback",), 5, "wasl.ml.registry:rollback",
                                 "Restores the previous production model."),
    "proposal.decide": HighImpact(("proposal:decide",), 5, "wasl.services.improve:decide_proposal",
                                  "Changes SLAs, reminder timing, message variants or channel order for everyone."),
    "ingest.match.resolve": HighImpact(("case:create",), 0, "wasl.services.ingest:resolve_match",
                                       "Attaches an unmatched result to a patient record."),
    "patient.deceased.record": HighImpact(("closure:authorise",), 10, "wasl.services.workflow:record_death",
                                          "Stops ALL patient messaging and outreach and sends every open case to clinical "
                                          "review; recording it against the wrong patient would silently stop their care."),
    "patient.deceased.revoke": HighImpact(("case:reopen",), 20, "wasl.services.workflow:revoke_death",
                                          "Corrects a death recorded in error (wrong patient): outreach may resume."),
    "content.signoff": HighImpact(("content:approve", "content:translation_review"), 5,
                                  "wasl.comms.governance:approve", "Makes patient-facing wording (or a translation) live."),
}


def require_signoff(actor: Actor, action: str, note: str | None = None) -> None:
    spec = HIGH_IMPACT[action]
    if not actor.is_human or actor.user_id is None:
        raise Forbidden(f"'{action}' is a high-impact action and needs explicit sign-off by a named person "
                        f"(automated actors are never allowed): {spec.why}")
    if not any(actor.can(p) for p in spec.perms):
        raise Forbidden(f"'{action}' requires permission {' or '.join(spec.perms)}")
    if spec.min_note and (not note or len(note.strip()) < spec.min_note):
        raise Invalid(f"'{action}' requires a written rationale of at least {spec.min_note} characters")
