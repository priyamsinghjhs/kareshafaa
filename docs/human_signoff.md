# Explicit human sign-off on high-impact actions (Iteration 7)

`wasl/signoff.py` is the single registry of high-impact actions. Each service function that performs one calls
`require_signoff(actor, action, rationale)` **before** it changes anything. The guard refuses:

* any automated actor (rules engine, SLA sweep, ML service, import/integration jobs) **even when it carries a
  privileged role** - it must be a named person (`is_human` and a user id);
* a person without the listed permission;
* a missing or too-short written rationale where one is required.

The action itself is then audited (hash-chained `AuditEvent` with the user, the rationale and the detail).
Automated actors may only ingest results, auto-triage non-urgent cases, send patient messages and raise alerts.

| Action | Permission | Rationale (min chars) | Performed by | Why it is high-impact |
|---|---|---|---|---|
| `case.close.authorised` | closure:authorise | 10 | `wasl.services.workflow:close_case` | Ends follow-up without clinical evidence (decline, unable to contact, moved...). |
| `case.close.evidence` | case:write | reason code / n.a. | `wasl.services.workflow:close_case` | Ends follow-up on recorded evidence; evidence is verified against the case. |
| `case.reopen` | case:reopen | 10 | `wasl.services.workflow:transition` | Restarts a closed pathway and its clocks. |
| `case.review.resolve` | case:triage_urgent | 10 | `wasl.services.workflow:resolve_review` | Clears a fail-safe clinical review flag; may LOWER the urgency tier. |
| `case.assign` | case:assign | 3 | `wasl.services.workflow:assign` | Changes the accountable owner. |
| `case.transfer.request` | case:transfer | 5 | `wasl.services.workflow:request_transfer` | Starts a two-key hand-over of accountability to another organisation. |
| `case.transfer.decide` | case:read | reason code / n.a. | `wasl.services.workflow:decide_transfer` | Accepts or declines accountability (a decline needs a reason). |
| `confirmatory.result` | confirm:result | reason code / n.a. | `wasl.services.workflow:record_confirmatory_result` | A negative confirmatory result moves the case towards closure/surveillance. |
| `referral.decide` | referral:decide | reason code / n.a. | `wasl.services.workflow:decide_referral` | Accepting or rejecting a referral (rejection needs a reason code and note). |
| `careplan.start` | careplan:write | 5 | `wasl.services.workflow:start_care` | Records that treatment or surveillance started: stops the pathway target clock. |
| `escalation.ack` | escalation:ack | 3 | `wasl.services.escalation:acknowledge_escalation` | Acknowledges an SLA / pathway-target alert. |
| `model.approve` | model:approve | 5 | `wasl.ml.registry:approve` | Puts a new outreach-priority model into production. |
| `model.reject` | model:approve | 5 | `wasl.ml.registry:reject` | Rejects a candidate model. |
| `model.rollback` | model:rollback | 5 | `wasl.ml.registry:rollback` | Restores the previous production model. |
| `proposal.decide` | proposal:decide | 5 | `wasl.services.improve:decide_proposal` | Changes SLAs, reminder timing, message variants or channel order for everyone. |
| `ingest.match.resolve` | case:create | reason code / n.a. | `wasl.services.ingest:resolve_match` | Attaches an unmatched result to a patient record. |
| `content.signoff` | content:approve or content:translation_review | 5 | `wasl.comms.governance:approve` | Makes patient-facing wording (or a translation) live. |
| `patient.deceased.record` | closure:authorise | 10 | `wasl.services.workflow:record_death` | Stops all patient messaging and outreach; every open case goes to clinical review (wrong patient = care silently stopped). |

Additional two-person rules already in place: a model's trainer cannot approve it (`model:train` vs
`model:approve` roles); transfers are two-key (the current owner stays accountable until the receiver accepts);
a translation needs a translator **and** a different clinician; tier lowering is only possible through
`case.review.resolve` (ORM interlock).

## Gaps closed in Iteration 7

Before this round, several functions checked only the permission, so an automated job holding a privileged role
could have acted: re-opening a closed case, reassigning, transfer request/decision, confirmatory result, referral
decision, starting care, acknowledging escalations, model reject/rollback, and deciding rule proposals. All now go
through the guard. Declining a transfer now needs a reason.

Tests: `tests/test_human_signoff.py` (static check that every registered function calls the guard with its
action name; guard behaviour for bots, missing permission and short rationale for all 18 actions; service
functions refuse automated actors before any state change; API re-open needs permission and a reason).
