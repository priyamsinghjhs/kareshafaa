"""Next-best-action draft agent (LangGraph).

Small free-hostable graph: gather facts in Python → draft (hosted SLM if configured,
else deterministic template) → safety check → plain-language draft.

Always advisory. requires_approval is always true. The staff member must approve
before any action is recorded. No PHI is sent to the model (scrubbed facts only).
"""
from __future__ import annotations

import threading
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage, convert_to_openai_messages
from langgraph.graph import END, START, StateGraph

from ..config import get_settings
from . import assist
from . import copilot as _cop

DISCLAIMER = "Draft only. A human must approve before any action is recorded."
SYSTEM = (
    "You draft a short plain-language next step for a screening follow-up coordinator. "
    "Use ONLY the facts given. Do not invent results, medicines, doses, or diagnoses. "
    "Do not say the action has already been done. Write 1-2 short sentences. "
    "Reply with the draft only."
)

class NextBestState(TypedDict, total=False):
    facts: dict
    use_model: bool
    messages: list
    raw: str
    origin: str
    text: str

_GRAPH = None
_LOCK = threading.Lock()

def _template(facts: dict) -> str:
    primary = facts.get("primary_label")
    state = facts.get("state_label") or facts.get("state")
    tier = facts.get("tier_label") or facts.get("tier")
    why = facts.get("why") or ""
    overdue = facts.get("overdue_days")
    escalation = facts.get("escalation_level")
    explain = f"This case is '{state}' at {tier} priority."
    if overdue:
        explain += f" It is {overdue} days past the step deadline."
    if escalation:
        explain += f" Escalation level is {escalation}."
    if why:
        explain += f" {why}"
    if primary:
        action = f"Recommended next step: {primary}."
        idle = "If no one acts, the case stays overdue and can escalate further."
    else:
        action = "No primary next step is available for your role right now."
        idle = "You can still view the record, reassign, or add a lesson."
    return (f"{explain} {action} {idle} "
            f"This is a draft for your review. Approve or edit before recording any action.")

def _node_prompt(state: NextBestState) -> NextBestState:
    facts = state.get("facts") or {}
    lines = [f"- {k.replace('_', ' ')}: {v}" for k, v in facts.items() if v not in (None, "", [])]
    human = "Write the next-step draft.\n\nFacts:\n" + "\n".join(lines) + "\n\nReply with the draft only."
    msgs = [SystemMessage(SYSTEM), HumanMessage(human)]
    return {"messages": convert_to_openai_messages(msgs)}

def _node_draft(state: NextBestState) -> NextBestState:
    if state.get("use_model"):
        s = get_settings()
        headers = {"Authorization": f"Bearer {s.slm_api_key}", "Content-Type": "application/json",
                   "HTTP-Referer": (s.public_base_url or assist.DEFAULT_REFERER), "X-Title": assist.APP_TITLE}
        payload = {"model": s.slm_model, "messages": state["messages"], "temperature": 0.2, "max_tokens": 180}
        try:
            data = assist._http_post(s.slm_base_url.rstrip("/") + "/chat/completions",
                                    headers=headers, payload=payload, timeout=float(s.slm_timeout_s))
            raw = data["choices"][0]["message"]["content"]
            if isinstance(raw, str) and raw.strip():
                return {"raw": raw.strip(), "origin": "model"}
        except Exception:
            pass
    return {"raw": _template(state.get("facts") or {}), "origin": "template"}

def _node_safety(state: NextBestState) -> NextBestState:
    facts = state.get("facts") or {}
    text = (state.get("raw") or "").strip()
    origin = state.get("origin") or "template"
    # reject claims that an action was already taken
    if assist.CLAIMS_ACTION.search(text):
        return {"text": _template(facts), "origin": "template"}
    # reject dosing or diagnosis language not present in facts
    if _cop._DOSE.search(text) or _cop._DIAGNOSIS.search(text):
        fact_text = " ".join(str(v) for v in facts.values())
        if not (_cop._DOSE.search(fact_text) or _cop._DIAGNOSIS.search(fact_text)):
            return {"text": _template(facts), "origin": "template"}
    if len(text) > 500:
        text = text[:500].rsplit(" ", 1)[0] + "."
    if not text:
        return {"text": _template(facts), "origin": "template"}
    return {"text": text, "origin": origin}

def _build():
    g = StateGraph(NextBestState)
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
        with _LOCK:
            if _GRAPH is None:
                _GRAPH = _build()
    return _GRAPH

def run(facts: dict, *, use_model: bool) -> dict:
    """Run the graph. Returns draft, origin, steps, requires_approval, disclaimer."""
    steps = ["facts gathered", "draft written", "safety checked"]
    try:
        out = graph().invoke({"facts": facts, "use_model": use_model})
        text = out.get("text") or _template(facts)
        origin = out.get("origin") or "template"
    except Exception:
        text = _template(facts)
        origin = "template"
    return {"draft": text, "origin": origin, "steps": steps,
            "requires_approval": True, "disclaimer": DISCLAIMER}
