# Explicit human sign-off on high-impact actions (Iteration 7)

`kareshafaa/signoff.py` is the single registry of high-impact actions. Each service function that performs one calls
`require_signoff(actor, action, rationale)` **before** it changes anything. The guard refuses:

* any automated actor (rules engine, SLA sweep, ML service, import/integration jobs) **even when it carries a
  privileged role** - it must be a named person (`is_human` and a user id);
* a person without the listed permission;
* a missing or too-short written rationale where one is required.

The action itself is then audited (hash-chained `AuditEvent` with the user, the rationale and the detail).
Automated actors may only ingest results, auto-triage non-urgent cases, send patient messages and raise alerts.

| Action | Permission | Rationale (min chars) | Performed by | Why it is high-impact |
|---|---|---|---|---|
| `case.close.authorised` | closure:authorise | 10 | `kareshafaa.services.workflow:close_case` | Ends follow-up without clinical evidence (decline, unable to contact, moved...). |
| `case.close.evidence` | case:write | reason code / n.a. | `kareshafaa.services.workflow:close_case` | Ends follow-up on recorded evidence; evidence is verified against the case. |
| `case.reopen` | case:reopen | 10 | `kareshafaa.services.workflow:transition` | Restarts a closed pathway and its clocks. |
| `case.review.resolve` | case:triage_urgent | 10 | `kareshafaa.services.workflow:resolve_review` | Clears a fail-safe clinical review flag; may LOWER the urgency tier. |
| `case.assign` | case:assign | 3 | `kareshafaa.services.workflow:assign` | Changes the accountable owner. |
| `case.transfer.request` | case:transfer | 5 | `kareshafaa.services.workflow:request_transfer` | Starts a two-key hand-over of accountability to another organisation. |
| `case.transfer.decide` | case:read | reason code / n.a. | `kareshafaa.services.workflow:decide_transfer` | Accepts or declines accountability (a decline needs a reason). |
| `confirmatory.result` | confirm:result | reason code / n.a. | `kareshafaa.services.workflow:record_confirmatory_result` | A negative confirmatory result moves the case towards closure/surveillance. |
| `referral.decide` | referral:decide | reason code / n.a. | `kareshafaa.services.workflow:decide_referral` | Accepting or rejecting a referral (rejection needs a reason code and note). |
| `careplan.start` | careplan:write | 5 | `kareshafaa.services.workflow:start_care` | Records that treatment or surveillance started: stops the pathway target clock. |
| `escalation.ack` | escalation:ack | 3 | `kareshafaa.services.escalation:acknowledge_escalation` | Acknowledges an SLA / pathway-target alert. |
| `model.approve` | model:approve | 5 | `kareshafaa.ml.registry:approve` | Puts a new outreach-priority model into production. |
| `model.reject` | model:approve | 5 | `kareshafaa.ml.registry:reject` | Rejects a candidate model. |
| `model.rollback` | model:rollback | 5 | `kareshafaa.ml.registry:rollback` | Restores the previous production model. |
| `proposal.decide` | proposal:decide | 5 | `kareshafaa.services.improve:decide_proposal` | Changes SLAs, reminder timing, message variants or channel order for everyone. |
| `ingest.match.resolve` | results:inbox (care coordinators) | reason code / n.a. | `kareshafaa.services.ingest:resolve_match` | Attaches an unmatched result to a patient record. |
| `content.signoff` | content:approve or content:translation_review | 5 | `kareshafaa.comms.governance:approve` | Makes patient-facing wording (or a translation) live. |
| `patient.deceased.record` | closure:authorise | 10 | `kareshafaa.services.workflow:record_death` | Stops all patient messaging and outreach; every open case goes to clinical review (wrong patient = care silently stopped). |
| `patient.deceased.revoke` | case:reopen | 20 | `kareshafaa.services.workflow:revoke_death` | Corrects a death recorded in error; open cases re-reviewed, cases closed as deceased listed for re-opening. |
| `consent.record` | consent:write | 10 | `kareshafaa.services.workflow:record_consent` | Staff record a consent decision on the patient's behalf: a withdrawal stops automated outreach in every programme (or one channel) and sends open cases to review; a permit resumes it or allows external sharing. The patient's own app decision is unaffected (round 19). |
| `case.decline.record` | consent:write | 10 | `kareshafaa.services.workflow:record_decline` | Staff record on the patient's behalf that they declined further care: the pathway stops and goes to declined review (round 19). |

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
action name; guard behaviour for bots, missing permission and short rationale for all 19 actions; service
functions refuse automated actors before any state change; API re-open needs permission and a reason).

## Gaps closed in round 19

An audit of every write endpoint against the registry found two kinds of high-impact path without explicit
sign-off:

* **Decisions recorded on the patient's behalf.** `record_consent` and `record_decline`, when called by staff,
  checked only `consent:write`. An automated job holding a coordinator role could withdraw a patient's
  care-coordination consent, which stops automated outreach in every programme, or record a decline, which stops the
  pathway. Neither needed a rationale. Both are now registered (`consent.record`, `case.decline.record`): a named
  person, `consent:write`, and a written rationale of at least 10 characters (how the patient gave the decision).
  A patient's own decision in the app is unchanged.
* **Evidence of a closed episode re-used after a re-open.** A re-opened case could reach
  `treatment_or_surveillance_started` through a generic transition on the *old* care plan, without the
  `careplan.start` sign-off. It could also be closed on the old care plan, the old fulfilled appointment or the old
  negative confirmatory result before the new result arrived. Care-plan, attendance, no-show and closure evidence,
  and the negative result that allows a surveillance plan, now count only if they were recorded since the last
  re-open (`workflow.episode_start`).

Endpoints reviewed and judged adequately covered: attendance, appointment booking, reconsider and the other generic
transitions (automated actors are limited to triage, notification and acknowledgement; all other targets need
the per-state permission and their evidence guard). Consult requests and replies, lessons, notification sends and
call records change no pathway state on their own. Model training and proposal generation create only candidates;
promotion and adoption are signed off. The SLA sweep and simulation run are operational. Ingest is automated by
design, with the fail-safe review.

Tests: `tests/test_signoff_round19.py`.
