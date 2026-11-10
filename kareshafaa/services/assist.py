"""Draft-text suggestions for staff clinical note fields (added 10 Nov 2026).

A small LangGraph graph - build prompt -> generate -> post-check - asks a free hosted healthcare small language model
(default: InclusionAI Ling 3.0 Flash Sante on OpenRouter, an OpenAI-compatible chat-completions API) for a short draft
the staff member can insert, edit or dismiss.

Rules (docs/clinical_safety_case.md H-41, docs/operating_modes.md):
  * advisory text only: nothing here closes, moves, assigns, files or sends anything - it returns a string or None;
  * off unless KARESHAFAA_SLM_API_KEY is set, and off whenever the AI kill switch is off (kareshafaa/ml/switch.py);
  * minimised context: only the case facts needed to draft the wording; patient name, MRN, Emirates ID, phone, email,
    address, birth year and staff names are never sent, and are scrubbed from any free text (including the draft);
  * fail closed: provider error, timeout, rate limit or a bad answer -> no suggestion (never an HTTP 500);
  * the prompt and the generated text are never stored (the audit event records only that a draft was asked for).
"""
from __future__ import annotations

import re
import threading
import time
from collections import deque
from typing import Any, TypedDict

import httpx
from langchain_core.messages import HumanMessage, SystemMessage, convert_to_openai_messages
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Appointment, CarePlan, Case, ConsultRequest, ServiceRequest
from ..programmes import PROGRAMMES, TIER_LABEL
from ..statemachine import AUTHORISED_CLOSURE_REASONS, LABELS

DISCLAIMER = "Draft only. Check against the record before you use it."
APP_TITLE = "KareShafaa"
DEFAULT_REFERER = "https://kareshafaa.onrender.com"

# what each supported form is for (the model is told which note it is drafting). Anything else is refused.
ACTIONS: dict[str, str] = {
    "triage": "note recorded when the clinician confirms triage of the screening result",
    "consultation": "note recorded when the consultation step is completed",
    "unreachable": "note explaining why the patient is being marked unreachable after the contact attempts",
    "call": "note on a phone call with the patient about their result",
    "notify": "note about notifying the patient of their result",
    "refer": "clinical summary sent with a referral to the specialist service",
    "decide_referral": "note on the referral decision (accept, or the reason for rejecting it)",
    "reschedule": "note on why the appointment is being moved",
    "cancel_appointment": "note on why the appointment is being cancelled",
    "start_care": "description of the care plan (treatment or surveillance) that has started",
    "close_evidence": "closure note when closing the case with linked evidence that care was reached",
    "close_authorised": "rationale for an authorised closure decision",
    "decline": "note recording what the patient said when declining further follow-up",
    "reconsider": "note on why follow-up is being resumed",
    "transfer": "reason and summary for the clinician receiving the case transfer",
    "decide_transfer": "note on the decision to accept or decline a case transfer",
    "consult": "question to a colleague in an e-consultation about this case",
    "consult_answer": "answer to a colleague's or the patient's consultation question",
    "consent": "note on how the patient gave their consent decision",
    "review": "clinical review note on the result and the patient context",
    "reopen": "reason for re-opening the case",
    "encounter_note": "clinical note of a consultation or encounter for the care team",
    "escalation_ack": "action being taken in response to an escalation alert",
    "summary_ack": "note acknowledging the care summary sent to the GP",
    "appointment_request_resolve": "note on how the patient's appointment change request was handled",
    "patient_request_decline": "note telling the team what happens next after declining a patient's appointment request",
}
FIELDS = frozenset({"note", "notes", "description", "summary", "question", "answer", "response", "rationale"})
EVIDENCE_TYPES = frozenset({"appointment_fulfilled", "care_plan", "negative_confirmatory_result"})
MAX_DRAFT = 2000
MAX_SUGGESTION = {"description": 600, "summary": 900, "question": 600}  # default 500 characters
CLAIMS_ACTION = re.compile(
    r"\b(i|we)\s+(have\s+|has\s+|'ve\s+)?(closed|sent|messaged|transferred|assigned|booked|referred|filed|moved|"
    r"cancelled|notified|discharged)\b|\bcase\s+(has\s+been\s+|is\s+(now\s+)?|was\s+)?closed\b|"
    r"\b(message|referral|sms)\s+(has\s+been\s+|was\s+)sent\b|\bas an ai\b|\blanguage model\b", re.I)

SYSTEM_PROMPT = (
    "You draft short professional wording for a healthcare staff member's note in a screening follow-up system. "
    "Rules: use ONLY the facts given. Do not invent results, values, medicines, doses, allergies, diagnoses, "
    "appointments, dates, names or anything the patient said. If a needed fact is missing, write a short placeholder "
    "in square brackets, e.g. [date of appointment]. Do not diagnose, do not change or suggest a different urgency, "
    "and never say that the case was closed, sent, booked or messaged - the staff member does that themselves. "
    "If the staff member has started a draft, continue or tidy it without changing its meaning. "
    "Write 1 to 3 short sentences in English, suitable for the named field. Reply with the note text only: "
    "no greeting, no heading, no quotation marks, no explanation."
)

# ------------------------------------------------------------------------------------------- sanitising (PHI)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_EID = re.compile(r"\b784[-\s]?\d{4}[-\s]?\d{7}[-\s]?\d\b")
_PHONE = re.compile(r"(?<![\w.])\+?\d[\d\s().-]{6,}\d(?![\w.])")
_LONGNUM = re.compile(r"\b[A-Z]{0,4}-?\d{6,}\b")
_DOB = re.compile(r"\b(dob|d\.o\.b|date of birth|born(\s+on|\s+in)?)\b\.?[:\s]*"
                  r"(\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4}|\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{4}|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}|\d{4})?",
                  re.I)


PATIENT_TEXT_IDENTIFIERS = ("phone", "email", "address_line", "nok_name", "nok_phone", "id_number", "insurance_member_id")
_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})$")


def _phone(m: re.Match) -> str:
    return m.group(0) if _DATE.match(m.group(0).strip()) else "[phone]"


def scrub(text: str | None, secrets: list[str] | None = None, limit: int = 600) -> str:
    """Remove direct identifiers from free text before it leaves the server."""
    if not text:
        return ""
    t = _EMAIL.sub("[email]", str(text))
    t = _EID.sub("[id]", t)
    t = _DOB.sub("[date of birth]", t)
    t = _PHONE.sub(_phone, t)
    t = _LONGNUM.sub("[id]", t)
    for s in sorted({x.strip() for x in (secrets or []) if x and len(x.strip()) >= 2}, key=len, reverse=True):
        t = re.sub(r"(?<!\w)" + re.escape(s) + r"(?!\w)", "[id]" if re.fullmatch(r"[\d\s+()-]+", s) else "[patient]", t,
                   flags=re.I)
    t = re.sub(r"[ \t]+", " ", t).strip()
    return t[:limit]


def _identifiers(c: Case, staff_names: list[str]) -> list[str]:
    p = c.patient
    out = [p.given_name, p.family_name, f"{p.given_name} {p.family_name}", p.mrn, p.national_id or "",
           str(p.birth_year or "")]
    for attr in PATIENT_TEXT_IDENTIFIERS:
        v = getattr(p, attr, None)
        if isinstance(v, str):
            out.append(v)
    for n in staff_names:
        n = (n or "").replace("Dr.", "").replace("Dr ", "").strip()
        if n:
            out.append(n)
            parts = n.split()
            if len(parts) > 1 and len(parts[-1]) >= 4:
                out.append(parts[-1])  # family name on its own ("Dr Rahman")
    return out


def build_context(db: Session, c: Case, *, action: str, field: str, evidence_type: str | None = None,
                  option: str | None = None, consult_id: int | None = None, draft: str = "",
                  staff_names: list[str] | None = None) -> dict:
    """The ONLY data sent to the model: minimised facts of this one case plus the scrubbed draft."""
    ids = _identifiers(c, staff_names or [])
    prog = PROGRAMMES.get(c.programme)
    ctx: dict[str, Any] = {
        "form": action, "form_purpose": ACTIONS[action], "field": field,
        "programme": prog.name if prog else c.programme,
        "urgency": TIER_LABEL.get(c.tier, c.tier), "case_state": LABELS.get(c.state, c.state),
    }
    rep = c.report
    if rep is not None:
        res = {"test": rep.display, "conclusion": rep.conclusion, "values": []}
        for o in rep.observations or []:
            v = o.value_text if o.value is None else f"{o.value:g}"
            res["values"].append(" ".join(x for x in (o.display, v or "", o.unit or "", f"(flag {o.interpretation})") if x))
        ctx["screening_result"] = res
    if c.pathway or c.target_text:
        ctx["pathway"] = scrub(" - ".join(x for x in (c.pathway, c.target_text) if x), ids, 200)
    if c.review_required:
        ctx["review_reasons"] = [scrub(str(r), ids, 120) for r in (c.review_reasons or [])][:5]
    if evidence_type in EVIDENCE_TYPES:
        ctx["evidence_type"] = evidence_type
        ev = _evidence(db, c, evidence_type, ids)
        if ev:
            ctx["evidence"] = ev
    if option and re.fullmatch(r"[a-z0-9_]{1,40}", option):
        ctx["selected_option"] = AUTHORISED_CLOSURE_REASONS.get(option, option.replace("_", " ")) \
            if action == "close_authorised" else option.replace("_", " ")
    if action == "consult_answer" and consult_id:
        q = db.get(ConsultRequest, consult_id)
        if q is not None and q.case_id == c.id:
            ctx["consult_question"] = scrub(q.question, ids, 600)
    if action == "decide_referral":
        r = db.scalar(select(ServiceRequest).where(ServiceRequest.case_id == c.id, ServiceRequest.kind == "referral")
                      .order_by(ServiceRequest.id.desc()).limit(1))
        if r is not None:
            ctx["referral"] = {"service": r.code, "status": r.status,
                               "clinical_summary": scrub(r.clinical_summary, ids, 600)}
    ctx["current_draft"] = scrub(draft, ids, MAX_DRAFT)
    return ctx


def _evidence(db: Session, c: Case, evidence_type: str, ids: list[str]) -> dict | None:
    if evidence_type == "care_plan":
        p = db.scalar(select(CarePlan).where(CarePlan.case_id == c.id).order_by(CarePlan.id.desc()).limit(1))
        return {"care_plan_kind": p.kind, "care_plan_description": scrub(p.description, ids, 600),
                "review_due": bool(p.review_due_on)} if p else None
    if evidence_type == "appointment_fulfilled":
        a = db.scalar(select(Appointment).where(Appointment.case_id == c.id, Appointment.status == "fulfilled")
                      .order_by(Appointment.id.desc()).limit(1))
        return {"appointment": "attended"} if a else None
    if evidence_type == "negative_confirmatory_result":
        r = db.scalar(select(ServiceRequest).where(ServiceRequest.case_id == c.id,
                                                   ServiceRequest.kind == "confirmatory_test",
                                                   ServiceRequest.result.is_not(None))
                      .order_by(ServiceRequest.id.desc()).limit(1))
        return {"confirmatory_test": r.code, "result": r.result} if r else None
    return None


# ------------------------------------------------------------------------------------------- LangGraph graph
class DraftState(TypedDict, total=False):
    context: dict
    messages: list
    raw: str | None
    suggestion: str | None
    error: str | None


def _http_post(url: str, *, headers: dict, payload: dict, timeout: float) -> dict:
    """The one outbound call (patched in tests; the real provider is never called from the test suite)."""
    with httpx.Client(timeout=timeout) as cl:
        r = cl.post(url, headers=headers, json=payload)
        r.raise_for_status()
        return r.json()


def _render_context(ctx: dict) -> str:
    lines = []
    for k, v in ctx.items():
        if k in ("current_draft",):
            continue
        if isinstance(v, dict):
            v = "; ".join(f"{a.replace('_', ' ')}: {', '.join(map(str, b)) if isinstance(b, list) else b}"
                          for a, b in v.items() if b not in (None, "", []))
        elif isinstance(v, list):
            v = "; ".join(map(str, v))
        lines.append(f"- {k.replace('_', ' ')}: {v}")
    draft = ctx.get("current_draft") or ""
    return ("Case facts:\n" + "\n".join(lines) + "\n\n"
            + (f"Staff member's draft so far:\n{draft}\n\n" if draft else "The field is empty.\n\n")
            + f"Write the {ctx.get('field', 'note')} for: {ctx.get('form_purpose', 'this note')}.")


def node_prompt(state: DraftState) -> DraftState:
    msgs = [SystemMessage(SYSTEM_PROMPT), HumanMessage(_render_context(state["context"]))]
    return {"messages": convert_to_openai_messages(msgs)}


def node_generate(state: DraftState) -> DraftState:
    s = get_settings()
    headers = {"Authorization": f"Bearer {s.slm_api_key}", "Content-Type": "application/json",
               "HTTP-Referer": (s.public_base_url or DEFAULT_REFERER), "X-Title": APP_TITLE}
    payload = {"model": s.slm_model, "messages": state["messages"], "temperature": 0.2, "max_tokens": 220}
    try:
        data = _http_post(s.slm_base_url.rstrip("/") + "/chat/completions", headers=headers, payload=payload,
                          timeout=float(s.slm_timeout_s))
        raw = data["choices"][0]["message"]["content"]
        if not isinstance(raw, str):
            return {"raw": None, "error": "bad_response"}
        return {"raw": raw}
    except httpx.TimeoutException:
        return {"raw": None, "error": "timeout"}
    except Exception:  # noqa: BLE001 - provider down, HTTP error, junk JSON: no suggestion, never a 500
        return {"raw": None, "error": "provider_error"}


def post_check(raw: str | None, field: str, ids: list[str] | None = None) -> str | None:
    """Return the cleaned suggestion, or None when it must not be shown."""
    if not raw or not isinstance(raw, str):
        return None
    t = re.sub(r"<think>.*?</think>", "", raw, flags=re.S | re.I).strip()
    t = re.sub(r"^(note|draft|suggestion|rationale|summary|answer|question|description)\s*:\s*", "", t, flags=re.I)
    t = t.strip().strip('"').strip("“”").strip()
    t = re.sub(r"\s+", " ", t)
    if len(t) < 3:
        return None
    if len(t) > MAX_SUGGESTION.get(field, 500):
        return None
    if CLAIMS_ACTION.search(t):
        return None
    if ids:  # the model must not bring identifiers back (it never saw them; belt and braces)
        if scrub(t, ids, 10_000) != t:
            t = scrub(t, ids, 10_000)
    return t


def node_check(state: DraftState) -> DraftState:
    ctx = state.get("context") or {}
    out = post_check(state.get("raw"), ctx.get("field", "note"))
    return {"suggestion": out, "error": state.get("error") or (None if out else "rejected")}


def _after_generate(state: DraftState) -> str:
    return "check" if state.get("raw") else END


def _build_graph():
    g = StateGraph(DraftState)
    g.add_node("prompt", node_prompt)
    g.add_node("generate", node_generate)
    g.add_node("check", node_check)
    g.add_edge(START, "prompt")
    g.add_edge("prompt", "generate")
    g.add_conditional_edges("generate", _after_generate, {"check": "check", END: END})
    g.add_edge("check", END)
    return g.compile()


_GRAPH = None
_GRAPH_LOCK = threading.Lock()


def graph():
    global _GRAPH
    if _GRAPH is None:
        with _GRAPH_LOCK:
            if _GRAPH is None:
                _GRAPH = _build_graph()
    return _GRAPH


def draft(context: dict) -> dict:
    """Run the graph for one minimised context. Returns {suggestion, error}."""
    try:
        out = graph().invoke({"context": context})
    except Exception:  # noqa: BLE001
        return {"suggestion": None, "error": "graph_error"}
    return {"suggestion": out.get("suggestion"), "error": out.get("error")}


# ------------------------------------------------------------------------------------------- availability, rate limit
def availability(db: Session) -> str | None:
    """None when drafts may be written; else 'off' (AI kill switch) or 'not_configured' (no API key)."""
    from ..ml import switch

    s = get_settings()
    try:
        if not switch.enabled(db):
            return "off"
    except Exception:  # noqa: BLE001 - unsure = off
        return "off"
    if not (s.slm_api_key or "").strip():
        return "not_configured"
    return None


def status(db: Session) -> dict:
    s = get_settings()
    reason = availability(db)
    return {"enabled": reason is None, "reason": reason, "configured": bool((s.slm_api_key or "").strip()),
            "model": s.slm_model if (s.slm_api_key or "").strip() else None}


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[Any, deque] = {}
        self._lock = threading.Lock()

    def allow(self, key: Any, per_minute: int, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] >= 60.0:
                q.popleft()
            if len(q) >= max(1, per_minute):
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


LIMITER = RateLimiter()
