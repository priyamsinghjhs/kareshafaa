"""Coordinator copilot: draft the next outreach message, summarise a case, or simplify a result letter.

A small LangGraph graph — gather facts (done in Python) → draft (hosted healthcare SLM, or a
deterministic template when the model is off or the AI kill switch is on) → safety check →
output with cited sources. Drafts are advisory: they are returned as editable text and never
sent, filed or used to change a case. The staff member edits and then uses the normal send or
note form, where every existing check still applies.

Safety: no diagnoses beyond the record, no dosing, no promises, no claim that a message was
already sent. Patient identifiers are stripped before any model call (``assist.scrub``).
"""
from __future__ import annotations

import re
import threading
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage, convert_to_openai_messages
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Appointment, Case, Communication, ServiceRequest, StepTask
from ..programmes import PROGRAMMES, TIER_LABEL
from ..statemachine import LABELS
from . import assist

TASKS = ("outreach", "summary", "simplify_letter")
LANGS = ("en", "ar", "hi", "ur")
DISCLAIMER = "Draft only. Check against the record, edit if needed, and send it yourself — nothing is sent automatically."
MAX_OUT = 900

# clinical terms that must already appear in the case facts if the draft uses them
_DIAGNOSIS = re.compile(
    r"\b(cancers?|carcinoma|malignan\w+|tumou?rs?|metastas\w+|cin\s*[123]|diabetes|"
    r"type\s*[12]\s*diabetes|hypertension|anaemia|anemia|infarct\w+|stroke)\b",
    re.I)
# Medicine doses only — lab units such as ug/g, mmol/mol and % must not trigger this.
_DOSE = re.compile(
    r"\b(take|start|stop|increase|decrease)\s+\d+\s*(mg|mcg|µg|ml|iu|units?|tablets?|capsules?)?"
    r"|\b\d+([.,]\d+)?\s*(mg|mcg|µg|micrograms?|milligrams?|tablets?|capsules?|puffs?|drops?)\b",
    re.I)
_PROMISE = re.compile(
    r"\b(guarantees?|will be cured|definitely (be )?(fine|ok|okay|cured|safe)|"
    r"no need to worry|nothing to worry|100\s?%\s*(safe|cured|fine|effective)|"
    r"we (will|shall) (cure|heal|fix you)|promise[sd]?( that)?)\b",
    re.I)

SYSTEM_PROMPT = (
    "You draft short wording for a care-coordinator in a screening follow-up programme. "
    "Use ONLY the facts given. Do not invent results, values, medicines, doses, diagnoses, "
    "appointments, dates, names, or anything the patient said. If a needed fact is missing, "
    "put a short placeholder in square brackets. Do not diagnose, do not change urgency, "
    "and never say that a message was sent, a case was closed, or an appointment was booked — "
    "the staff member does that themselves. Do not promise a cure or a definite outcome. "
    "Do not give a medicine name or a dose. Reply with the draft text only: no heading, no "
    "quotation marks, no explanation, no greeting that uses a name (you do not have the name)."
)


class CopilotState(TypedDict, total=False):
    context: dict
    task: str
    language: str
    use_model: bool
    sources: list[str]
    messages: list
    raw: str | None
    text: str | None
    origin: str
    error: str | None


# ------------------------------------------------------------------------------------------- facts
def gather(db: Session, c: Case, *, task: str, language: str, staff_names: list[str] | None = None) -> dict:
    """Minimised, scrubbed case facts. The only data that may reach the model."""
    ids = assist._identifiers(c, staff_names or [])
    prog = PROGRAMMES.get(c.programme)
    ctx: dict[str, Any] = {
        "task": task,
        "programme": prog.name if prog else c.programme,
        "urgency": TIER_LABEL.get(c.tier, c.tier),
        "case_state": LABELS.get(c.state, c.state),
        "language": language if language in LANGS else "en",
    }
    sources = ["programme", "urgency", "case_state"]
    if c.due_at:
        ctx["deadline"] = c.due_at.strftime("%d/%m/%Y")
        sources.append("deadline")
    if c.pathway or c.target_text:
        ctx["pathway"] = assist.scrub(" — ".join(x for x in (c.pathway, c.target_text) if x), ids, 200)
        sources.append("pathway")
    rep = c.report
    if rep is not None:
        res = {"test": assist.scrub(rep.display, ids, 160), "conclusion": rep.conclusion, "values": []}
        for o in rep.observations or []:
            v = o.value_text if o.value is None else f"{o.value:g}"
            res["values"].append(assist.scrub(
                " ".join(x for x in (o.display, v or "", o.unit or "") if x), ids, 120))
        ctx["screening_result"] = res
        sources.append("screening_result")
    step = db.scalar(select(StepTask).where(StepTask.case_id == c.id, StepTask.status == "in-progress")
                     .order_by(StepTask.id.desc()).limit(1))
    if step is not None:
        ctx["current_step"] = assist.scrub(step.state.replace("_", " "), ids, 80)
        sources.append("current_step")
    last = db.scalar(select(Communication).where(Communication.case_id == c.id,
                                                  Communication.patient_id.is_not(None))
                     .order_by(Communication.sent_at.desc()).limit(1))
    if last is not None:
        ctx["last_outreach"] = {
            "channel": last.channel,
            "status": last.status,
            "when": last.sent_at.strftime("%d/%m/%Y") if last.sent_at else None,
        }
        sources.append("last_outreach")
    appt = db.scalar(select(Appointment).where(Appointment.case_id == c.id,
                                               Appointment.status == "booked")
                     .order_by(Appointment.start.desc()).limit(1))
    if appt is not None:
        ctx["open_appointment"] = {"status": appt.status, "when": appt.start.strftime("%d/%m/%Y %H:%M") if appt.start else None}
        sources.append("open_appointment")
    test = db.scalar(select(ServiceRequest).where(ServiceRequest.case_id == c.id)
                     .order_by(ServiceRequest.id.desc()).limit(1))
    if test is not None:
        ctx["latest_request"] = {"kind": test.kind, "code": assist.scrub(test.code, ids, 80), "status": test.status}
        sources.append("latest_request")
    ctx["sources"] = sources
    return ctx


# ------------------------------------------------------------------------------------------- templates (deterministic)
def _result_bit(ctx: dict, lang: str) -> str:
    res = ctx.get("screening_result") or {}
    test = res.get("test") or "screening"
    vals = ", ".join(res.get("values") or [])
    conclusion = res.get("conclusion") or ""
    if lang == "ar":
        extra = f"، النتيجة: {vals}" if vals else ""
        return f"فحص {test}{extra}"
    if lang == "hi":
        extra = f", परिणाम: {vals}" if vals else ""
        return f"{test}{extra}"
    if lang == "ur":
        extra = f"، نتیجہ: {vals}" if vals else ""
        return f"{test}{extra}"
    extra = f", result: {vals}" if vals else ""
    # conclusion is a code (actionable / needs_review / normal) — say it plainly, not as a diagnosis
    label = {"actionable": "needs follow-up", "needs_review": "needs a clinician to review",
             "normal": "no follow-up indicated"}.get(conclusion, "")
    return f"{test}{extra}" + (f" ({label})" if label else "")


def _next_bit(ctx: dict, lang: str) -> str:
    nxt = ctx.get("pathway") or ctx.get("current_step") or ""
    if not nxt:
        return ""
    if lang == "ar":
        return f" الخطوة التالية المسجّلة: {nxt}."
    if lang == "hi":
        return f" रिकॉर्ड में अगला कदम: {nxt}."
    if lang == "ur":
        return f" ریکارڈ میں اگلا قدم: {nxt}."
    return f" The recorded next step is: {nxt}."


def template(ctx: dict) -> str:
    """Deterministic draft used when the model is off, the kill switch is on, or the model fails the safety check."""
    task, lang = ctx.get("task") or "outreach", ctx.get("language") or "en"
    if lang not in LANGS:
        lang = "en"
    prog = ctx.get("programme") or "screening"
    result = _result_bit(ctx, lang)
    nxt = _next_bit(ctx, lang)
    due = ctx.get("deadline")
    last = ctx.get("last_outreach") or {}
    appt = ctx.get("open_appointment") or {}

    if task == "summary":
        bits = [f"{prog}. Urgency: {ctx.get('urgency')}. Current step: {ctx.get('case_state')}."]
        if result:
            bits.append(f"Screening: {result}.")
        if ctx.get("pathway"):
            bits.append(f"Pathway: {ctx['pathway']}.")
        if due:
            bits.append(f"Deadline: {due}.")
        if last:
            bits.append(f"Last outreach: {last.get('channel')} on {last.get('when')} ({last.get('status')}).")
        if appt:
            bits.append(f"Open appointment: {appt.get('status')} at {appt.get('when')} UAE.")
        if ctx.get("latest_request"):
            r = ctx["latest_request"]
            bits.append(f"Latest request: {r.get('kind')} {r.get('code')} ({r.get('status')}).")
        bits.append("This is a factual summary of the record, not a clinical opinion.")
        # summaries are for staff; keep English (the case page is English) and AR when asked
        if lang == "ar":
            return (f"{prog}. درجة الاستعجال: {ctx.get('urgency')}. الخطوة الحالية: {ctx.get('case_state')}. "
                    f"{('الفحص: ' + result + '. ') if result else ''}"
                    f"{('الموعد النهائي: ' + due + '. ') if due else ''}"
                    "هذا ملخص وقائع من السجل، وليس رأياً سريرياً.")
        return " ".join(bits)

    if task == "simplify_letter":
        if lang == "ar":
            return (f"هذه رسالة من فريق متابعة {prog}. {result}. {nxt.strip()} "
                    "يرجى الاتصال بالعيادة أو استخدام تطبيق المريض إذا كانت لديكم أسئلة. "
                    "هذه الرسالة تشرح ما هو مسجّل في ملفكم وليست تشخيصاً جديداً ولا تغيّر أي نتيجة أُعطيت لكم.")
        if lang == "hi":
            return (f"यह पत्र {prog} फॉलो-अप टीम की ओर से है। {result}।{nxt} "
                    "यदि आपके कोई प्रश्न हैं तो क्लिनिक से संपर्क करें या रोगी ऐप का उपयोग करें। "
                    "यह पत्र आपके रिकॉर्ड में दर्ज जानकारी को सरल शब्दों में बताता है; यह नया निदान नहीं है और पहले दी गई रिपोर्ट को नहीं बदलता।")
        if lang == "ur":
            return (f"یہ خط {prog} کی فالو اپ ٹیم کی جانب سے ہے۔ {result}۔{nxt} "
                    "اگر آپ کے کوئی سوال ہیں تو کلینک سے رابطہ کریں یا مریض کی ایپ استعمال کریں۔ "
                    "یہ خط آپ کے ریکارڈ میں درج معلومات کو سادہ الفاظ میں بیان کرتا ہے؛ یہ نئی تشخیص نہیں ہے اور پہلے دی گئی رپورٹ کو نہیں بدلتا۔")
        return (f"This letter is from the {prog} follow-up team. {result}.{nxt} "
                "Please contact the clinic or use the patient app if you have questions. "
                "This letter explains what is already in your record, in plain language. "
                "It is not a new diagnosis and does not change any result already given to you.")

    # outreach
    if lang == "ar":
        return (f"هذه رسالة من فريق متابعة {prog}. نتيجة الفحص جاهزة.{nxt} "
                "يرجى الرد على هذه الرسالة، أو استخدام تطبيق المريض، أو الاتصال بالعيادة. "
                "هذه الرسالة ليست تشخيصاً ولا تغيّر أي نتيجة أُعطيت لكم.")
    if lang == "hi":
        return (f"यह संदेश {prog} फॉलो-अप टीम की ओर से है। आपकी स्क्रीनिंग रिपोर्ट तैयार है।{nxt} "
                "कृपया इस संदेश का उत्तर दें, रोगी ऐप का उपयोग करें, या क्लिनिक को कॉल करें। "
                "यह संदेश निदान नहीं है और पहले दी गई किसी भी रिपोर्ट को नहीं बदलता।")
    if lang == "ur":
        return (f"یہ پیغام {prog} کی فالو اپ ٹیم کی جانب سے ہے۔ آپ کی اسکریننگ رپورٹ تیار ہے۔{nxt} "
                "براہ کرم اس پیغام کا جواب دیں، مریض کی ایپ استعمال کریں، یا کلینک کو کال کریں۔ "
                "یہ پیغام تشخیص نہیں ہے اور پہلے دی گئی کسی بھی رپورٹ کو نہیں بدلتا۔")
    return (f"This is the {prog} follow-up team. Your screening result is ready.{nxt} "
            "Please reply to this message, use the patient app, or call the clinic. "
            "This message is not a diagnosis and does not change any result already given to you.")


# ------------------------------------------------------------------------------------------- safety
def _facts_blob(ctx: dict) -> str:
    parts = []
    for k, v in ctx.items():
        if k in ("sources", "task", "language"):
            continue
        parts.append(str(v))
    return " ".join(parts).lower()


def safety_check(raw: str | None, ctx: dict) -> str | None:
    """Return cleaned text, or None when the draft must not be shown (model path falls back to a template)."""
    if not raw or not isinstance(raw, str):
        return None
    t = re.sub(r"<think>.*?</think>", "", raw, flags=re.S | re.I).strip()
    t = re.sub(r"^(draft|suggestion|letter|message|summary|note)\s*:\s*", "", t, flags=re.I)
    t = t.strip().strip('"').strip("“”").strip()
    t = re.sub(r"[ \t]+", " ", t)
    if len(t) < 8:
        return None
    if len(t) > MAX_OUT:
        t = t[:MAX_OUT].rsplit(" ", 1)[0]
    if assist.CLAIMS_ACTION.search(t) or _DOSE.search(t) or _PROMISE.search(t):
        return None
    facts = _facts_blob(ctx)
    for m in _DIAGNOSIS.finditer(t):
        term = m.group(0).lower()
        if term not in facts and term.rstrip("s") not in facts:
            return None
    return t


# ------------------------------------------------------------------------------------------- LangGraph
_GRAPH = None
_GRAPH_LOCK = threading.Lock()


def _node_prompt(state: CopilotState) -> CopilotState:
    ctx = state["context"]
    task = state["task"]
    lang = state["language"]
    purpose = {
        "outreach": "the next outreach message to the patient (the staff member will send it themselves)",
        "summary": "a short factual summary of the case for a coordinator (not a clinical opinion)",
        "simplify_letter": "a plain-language result letter the staff member can edit and send",
    }[task]
    lang_name = {"en": "English", "ar": "Arabic", "hi": "Hindi", "ur": "Urdu"}[lang]
    facts = []
    for k, v in ctx.items():
        if k in ("sources", "task", "language"):
            continue
        if isinstance(v, dict):
            v = "; ".join(f"{a}: {b}" for a, b in v.items() if b not in (None, "", []))
        elif isinstance(v, list):
            v = "; ".join(map(str, v))
        facts.append(f"- {k.replace('_', ' ')}: {v}")
    human = (f"Write {purpose} in {lang_name}.\n\nCase facts:\n" + "\n".join(facts) +
             "\n\nReply with the draft only.")
    msgs = [SystemMessage(SYSTEM_PROMPT), HumanMessage(human)]
    return {"messages": convert_to_openai_messages(msgs)}


def _node_draft(state: CopilotState) -> CopilotState:
    if state.get("use_model"):
        s = get_settings()
        headers = {"Authorization": f"Bearer {s.slm_api_key}", "Content-Type": "application/json",
                   "HTTP-Referer": (s.public_base_url or assist.DEFAULT_REFERER), "X-Title": assist.APP_TITLE}
        payload = {"model": s.slm_model, "messages": state["messages"], "temperature": 0.2, "max_tokens": 400}
        try:
            data = assist._http_post(s.slm_base_url.rstrip("/") + "/chat/completions",
                                    headers=headers, payload=payload, timeout=float(s.slm_timeout_s))
            raw = data["choices"][0]["message"]["content"]
            if isinstance(raw, str) and raw.strip():
                return {"raw": raw, "origin": "model"}
        except Exception:  # noqa: BLE001 — provider down, timeout, junk: fall through to the template
            pass
    return {"raw": template(state["context"]), "origin": "template"}


def _node_safety(state: CopilotState) -> CopilotState:
    ctx = state["context"]
    text = safety_check(state.get("raw"), ctx)
    origin = state.get("origin") or "template"
    if text is None:
        text = safety_check(template(ctx), ctx)
        origin = "template"
        if text is None:  # should never happen: templates are written to pass
            return {"text": None, "origin": origin, "error": "safety"}
    return {"text": text, "origin": origin, "error": None}


def _build_graph():
    g = StateGraph(CopilotState)
    g.add_node("prompt", _node_prompt)
    g.add_node("draft", _node_draft)
    g.add_node("safety", _node_safety)
    g.add_edge(START, "prompt")
    g.add_edge("prompt", "draft")
    g.add_edge("draft", "safety")
    g.add_edge("safety", END)
    return g.compile()


def graph():
    global _GRAPH
    if _GRAPH is None:
        with _GRAPH_LOCK:
            if _GRAPH is None:
                _GRAPH = _build_graph()
    return _GRAPH


def run(context: dict, *, task: str, language: str, use_model: bool) -> dict:
    """Run the graph. Returns {text, origin, sources, error, disclaimer}."""
    ctx = {**context, "task": task, "language": language}
    sources = list(ctx.get("sources") or [])
    try:
        out = graph().invoke({"context": ctx, "task": task, "language": language, "use_model": use_model,
                              "sources": sources})
    except Exception:  # noqa: BLE001
        text = safety_check(template(ctx), ctx)
        return {"text": text, "origin": "template", "sources": sources, "error": None if text else "graph_error",
                "disclaimer": DISCLAIMER}
    return {"text": out.get("text"), "origin": out.get("origin") or "template", "sources": sources,
            "error": out.get("error"), "disclaimer": DISCLAIMER}


def available_for_model(db: Session) -> bool:
    """True only when the SLM may be called (API key present and the AI kill switch is on)."""
    return assist.availability(db) is None
