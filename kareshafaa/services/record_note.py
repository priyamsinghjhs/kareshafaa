"""Deterministic, record-sourced note suggestions for case-action forms.

Clinical closure notes are health-critical: every sentence is filled from a fixed template
and only from structured data already on the case. A missing fact omits its sentence; nothing
is guessed and no language model is used. The clinician reviews and may edit the note before
saving; the suggestion never submits the form.

Why not a language model: no model can honestly promise 100% accuracy on clinical facts.
A template filled from the record is testable against the database — every number, unit and
date in the note equals the stored value.

Dates are shown in UAE time (Asia/Dubai) and labelled as such. Notes are offered in English
and Arabic (staff UI languages).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..calendar_uae import to_local
from ..models import (
    Appointment, CallTask, CarePlan, Case, Communication, DiagnosticReport, Observation,
    Organization, Practitioner, QuestionnaireResponse, ServiceRequest, StepTask,
)
from ..programmes import PROGRAMMES
from ..statemachine import AUTHORISED_CLOSURE_REASONS, LABELS, S
from . import workflow as wf

DISCLAIMER = {
    "en": "Suggested from the record — review before saving",
    "ar": "مقترحة من السجل — يُرجى المراجعة قبل الحفظ",
}
KIND = "from_the_record"
MAX_NOTE = 2000
LANGS = frozenset({"en", "ar"})

# Forms that get a record-sourced suggestion (and on which the SLM draft is not offered).
ACTIONS = frozenset({"close_evidence", "close_authorised", "unreachable", "lesson"})
EVIDENCE_TYPES = frozenset({"care_plan", "appointment_fulfilled", "negative_confirmatory_result"})

EVIDENCE_LABEL = {
    "en": {
        "care_plan": "care plan",
        "appointment_fulfilled": "attended appointment",
        "negative_confirmatory_result": "negative confirmatory result",
    },
    "ar": {
        "care_plan": "خطة رعاية",
        "appointment_fulfilled": "موعد حضره المريض",
        "negative_confirmatory_result": "نتيجة فحص تأكيدي سلبية",
    },
}
KIND_LABEL = {
    "en": {"treatment": "treatment", "surveillance": "surveillance"},
    "ar": {"treatment": "علاج", "surveillance": "متابعة"},
}
REASON_LABEL = {
    "en": dict(AUTHORISED_CLOSURE_REASONS),
    "ar": {
        "informed_decline": "قرر المريض عن علم رفض الرعاية (موثّق)",
        "unable_to_contact": "تعذر التواصل بعد استكمال سلم التواصل بالكامل",
        "deceased": "المريض متوفى",
        "moved_abroad": "انتقل المريض خارج البلاد؛ قُدّمت الخلاصة",
        "care_elsewhere_verified": "تلقى الرعاية في مكان آخر وتم التحقق من ذلك",
        "clinically_not_indicated": "الرعاية الإضافية غير مستطبة سريرياً (مسوّغ الطبيب)",
        "duplicate": "ملف مكرر دُمج في ملف آخر",
    },
}
PROGRAMME_NAME = {
    "en": {k: p.name for k, p in PROGRAMMES.items()},
    "ar": {
        "colorectal_fit": "فحص الأمعاء (القولون والمستقيم) - FIT",
        "cervical": "فحص عنق الرحم - HPV",
        "breast": "فحص الثدي - التصوير الشعاعي",
        "diabetes_hba1c": "فحص السكري - HbA1c / سكر الصائم",
    },
}
STATE_LABEL = {
    "en": dict(LABELS),
    "ar": {
        S.ISSUED: "صدرت النتيجة", S.TRIAGED: "فُرزت", S.PATIENT_NOTIFIED: "أُبلِغ المريض",
        S.ACKNOWLEDGED: "أُكِّد الفهم (اجتياز التحقق)", S.CONFIRMATORY_TEST: "فحص تأكيدي",
        S.CONSULTATION: "استشارة", S.REFERRAL_SENT: "أُرسل التحويل", S.REFERRAL_ACCEPTED: "قُبل التحويل",
        S.REFERRAL_REJECTED: "رُفض التحويل", S.APPOINTMENT_BOOKED: "حُجز موعد",
        S.APPOINTMENT_ATTENDED: "حُضر الموعد", S.TREATMENT_STARTED: "بدأ العلاج / المتابعة",
        S.CLOSED: "مغلق", S.UNREACHABLE: "تعذر التواصل", S.NO_SHOW: "لم يحضر",
        S.DECLINED_REVIEW: "رفض — مراجعة الطبيب",
    },
}
# English month abbreviations are fixed (not locale-dependent) so a sentence can be checked against the DB.
_MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_MONTHS_AR = ("يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
              "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر")
TZ_LABEL = "Asia/Dubai"


@dataclass(frozen=True)
class Fact:
    """One record-backed fact: a sentence, the raw values it was filled from, and a source line."""
    key: str
    values: dict[str, Any]
    source_field: str
    source_value: str
    source_href: str
    sentence: str


@dataclass(frozen=True)
class Suggestion:
    note: str
    facts: tuple[Fact, ...]
    disclaimer: str
    kind: str
    fingerprint: str
    lang: str

    @property
    def fact_keys(self) -> list[str]:
        return [f.key for f in self.facts]

    @property
    def sentences(self) -> list[str]:
        return [f.sentence for f in self.facts]

    @property
    def sources(self) -> list[dict[str, str]]:
        return [{"field": f.source_field, "value": f.source_value, "href": f.source_href, "key": f.key}
                for f in self.facts]


def format_number(value: float | int) -> str:
    """Shortest decimal that equals the stored number (250.0 → '250', 6.5 → '6.5')."""
    v = float(value)
    if v == int(v) and abs(v) < 10**12:
        return str(int(v))
    return format(v, ".15g")


def format_dt(utc_naive: datetime, lang: str = "en") -> str:
    """UAE wall-clock from a naive-UTC instant, always labelled Asia/Dubai."""
    local = to_local(utc_naive)
    if lang == "ar":
        return (f"{local.day:02d} {_MONTHS_AR[local.month - 1]} {local.year}، "
                f"{local.hour:02d}:{local.minute:02d} ({TZ_LABEL})")
    return (f"{local.day:02d} {_MONTHS_EN[local.month - 1]} {local.year}, "
            f"{local.hour:02d}:{local.minute:02d} ({TZ_LABEL})")


def format_date(d: date, lang: str = "en") -> str:
    """Calendar date (no clock, no zone) — used for date-only columns such as review_due_on."""
    if lang == "ar":
        return f"{d.day:02d} {_MONTHS_AR[d.month - 1]} {d.year}"
    return f"{d.day:02d} {_MONTHS_EN[d.month - 1]} {d.year}"


def flatten(text: str | None) -> str:
    return " ".join(str(text).split()) if text else ""


def fingerprint(note: str, keys: list[str]) -> str:
    payload = note + "\n" + ",".join(keys)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _href(case_id: int, anchor: str) -> str:
    return f"/cases/{case_id}#{anchor}"


def _lang(lang: str | None) -> str:
    return lang if lang in LANGS else "en"


# --------------------------------------------------------------------------- fact extractors (omit if a required piece is missing)

def _closure_fact(case: Case, *, action: str, evidence_type: str | None, option: str | None,
                  lang: str, evidence_ok: bool) -> Fact | None:
    if action == "close_evidence" and evidence_type in EVIDENCE_TYPES and evidence_ok:
        label = EVIDENCE_LABEL[lang][evidence_type]
        if lang == "ar":
            sentence = f"إغلاق هذا الملف مع ربط الدليل: {label}."
            field = "نوع الإغلاق"
        else:
            sentence = f"Closing this case with linked evidence: {label}."
            field = "Closure type"
        return Fact("closure_type", {"action": action, "evidence_type": evidence_type, "label": label},
                    field, label, _href(case.id, "act-h"), sentence)
    if action == "close_authorised":
        if option and option in AUTHORISED_CLOSURE_REASONS:
            label = REASON_LABEL[lang][option]
            values = {"action": action, "reason": option, "label": label}
            src = label
        elif option:
            return None  # unknown reason: do not invent a label
        else:
            label = None
            values = {"action": action}
            src = "authorised decision" if lang == "en" else "قرار مصرّح"
        if lang == "ar":
            sentence = (f"إغلاق هذا الملف بقرار مصرّح: {label}." if label
                        else "إغلاق هذا الملف بقرار مصرّح.")
            field = "نوع الإغلاق"
        else:
            sentence = (f"Closing this case as an authorised decision: {label}." if label
                        else "Closing this case as an authorised decision.")
            field = "Closure type"
        return Fact("closure_type", values, field, src, _href(case.id, "act-h"), sentence)
    if action == "unreachable":
        label = STATE_LABEL[lang].get(S.UNREACHABLE, "unreachable")
        if lang == "ar":
            sentence, field = f"وسم هذا الملف كـ«{label}».", "الإجراء"
        else:
            sentence, field = f"Marking this case as {label}.", "Action"
        return Fact("closure_type", {"action": action, "label": label},
                    field, label, _href(case.id, "act-h"), sentence)
    return None


def _programme_fact(case: Case, lang: str) -> Fact | None:
    if not case.programme:
        return None
    name = PROGRAMME_NAME[lang].get(case.programme) or PROGRAMME_NAME["en"].get(case.programme)
    if not name:
        return None
    if lang == "ar":
        sentence, field = f"برنامج الفحص: {name}.", "برنامج الفحص"
    else:
        sentence, field = f"Screening programme: {name}.", "Screening programme"
    return Fact("programme", {"programme": case.programme, "name": name},
                field, name, _href(case.id, "rs-h"), sentence)


def _result_fact(case: Case, lang: str) -> Fact | None:
    rep: DiagnosticReport | None = case.report
    if rep is None:
        return None
    test = (rep.display or "").strip()
    if not test:
        return None
    issued = format_dt(rep.issued_at, lang) if rep.issued_at else None
    parts, obs_values = [], []
    for o in list(rep.observations or []):
        bits = []
        display = (o.display or "").strip()
        if display and display != test:
            bits.append(display)
        raw_value = o.value
        raw_text = (o.value_text or "").strip() or None
        raw_unit = (o.unit or "").strip() or None
        raw_flag = (o.interpretation or "").strip() or None
        if raw_value is not None:
            bits.append(format_number(raw_value))
        elif raw_text:
            bits.append(raw_text)
        if raw_unit:
            bits.append(raw_unit)
        if raw_flag:
            bits.append(f"(flag {raw_flag})" if lang == "en" else f"(العلم {raw_flag})")
        if bits:
            parts.append(" ".join(bits))
        obs_values.append({"id": o.id, "display": display or None, "value": raw_value,
                           "value_text": raw_text, "unit": raw_unit, "flag": raw_flag})
    body = ": " + "; ".join(parts) if parts else ""
    conclusion = (rep.conclusion or "").strip() or None
    # conclusion is only added when there are no observation parts (otherwise it can look invented next to a value)
    conc = ""
    if conclusion and not parts:
        conc = f" ({conclusion})"
    if not issued:
        return None  # a result without a date: omit the sentence rather than invent a time
    if lang == "ar":
        sentence = f"النتيجة المسببة للملف: {test}{body}{conc} في {issued}."
        field = "النتيجة المسببة"
    else:
        sentence = f"Triggering result: {test}{body}{conc} on {issued}."
        field = "Triggering result"
    src = flatten(f"{test}{body}{conc} · {issued}")
    values: dict[str, Any] = {
        "test": test, "conclusion": conclusion, "date_utc": rep.issued_at, "date_display": issued,
        "report_id": rep.id, "observations": obs_values,
    }
    if len(obs_values) == 1:
        ov = obs_values[0]
        values.update({k: ov[k] for k in ("value", "value_text", "unit", "flag")})
    return Fact("triggering_result", values, field, src, _href(case.id, "rs-h"), sentence)


def _care_plan_fact(db: Session, case: Case, lang: str, *, required: bool) -> Fact | None:
    plan = _evidence_row(db, case, "care_plan")
    if plan is None:
        return None
    kind = KIND_LABEL[lang].get(plan.kind, plan.kind)
    when = format_dt(plan.created_at, lang) if plan.created_at else None
    if not when:
        return None
    author = _pname(db, plan.recorded_by_id)
    title = flatten(plan.description)
    review = format_date(plan.review_due_on, lang) if plan.review_due_on else None
    # Prefer a sentence with the title when it fits; otherwise drop the title (it stays in the source).
    def _sent(with_title: bool) -> str:
        t = f" «{title}»" if with_title and title else ""
        if lang == "ar":
            a = f" بواسطة {author}" if author else ""
            r = f" موعد المراجعة في {review}." if review else ""
            end = "." if not review else ""
            return f"خطة الرعاية المرتبطة رقم {plan.id} ({kind}){t} سُجِّلت في {when}{a}{end}{r}"
        a = f" by {author}" if author else ""
        r = f" Review due on {review}." if review else ""
        end = "." if not review else ""
        return f"Linked care plan #{plan.id} ({kind}){t} was recorded on {when}{a}{end}{r}"

    sentence = _sent(True)
    used_title = bool(title)
    if len(sentence) > 900:
        sentence = _sent(False)
        used_title = False
    field = "خطة الرعاية" if lang == "ar" else "Care plan"
    src_bits = [f"#{plan.id}", kind, title or "", when, author or "", (f"review {review}" if review else "")]
    src = flatten(" · ".join(b for b in src_bits if b))
    values = {"id": plan.id, "kind": plan.kind, "kind_label": kind, "title": title or None,
              "title_in_sentence": used_title, "date_utc": plan.created_at, "date_display": when,
              "author": author, "author_id": plan.recorded_by_id,
              "review_due_on": plan.review_due_on, "review_due_display": review}
    return Fact("care_plan", values, field, src, _href(case.id, "case-timeline"), sentence)


def _appointment_evidence_fact(db: Session, case: Case, lang: str) -> Fact | None:
    appt = _evidence_row(db, case, "appointment_fulfilled")
    if appt is None:
        return None
    start = format_dt(appt.start, lang) if appt.start else None
    if not start:
        return None
    attended = format_dt(appt.updated_at, lang) if appt.updated_at else None
    clinician = _pname(db, appt.practitioner_id)
    org = _oname(db, appt.org_id)
    if lang == "ar":
        where = ""
        if clinician:
            where += f" مع {clinician}"
        if org:
            where += f" في {org}"
        att = f" في {attended}" if attended else ""
        sentence = f"الموعد المرتبط رقم {appt.id}، والمجدول في {start}{where}، مسجَّل كحضور{att}."
        field = "الموعد"
    else:
        where = ""
        if clinician:
            where += f" with {clinician}"
        if org:
            where += f" at {org}"
        att = f" on {attended}" if attended else ""
        sentence = f"Linked appointment #{appt.id}, scheduled for {start}{where}, is recorded as attended{att}."
        field = "Appointment"
    src = flatten(" · ".join(b for b in (f"#{appt.id}", start, clinician, org, attended) if b))
    values = {"id": appt.id, "start_utc": appt.start, "start_display": start,
              "attended_utc": appt.updated_at, "attended_display": attended,
              "clinician": clinician, "clinician_id": appt.practitioner_id,
              "org": org, "org_id": appt.org_id}
    return Fact("appointment_fulfilled", values, field, src, _href(case.id, "case-timeline"), sentence)


def _confirmatory_fact(db: Session, case: Case, lang: str) -> Fact | None:
    row = _evidence_row(db, case, "negative_confirmatory_result")
    if row is None or not row.result:
        return None
    when = format_dt(row.decided_at or row.created_at, lang) if (row.decided_at or row.created_at) else None
    if not when:
        return None
    code = (row.code or "").strip()
    if not code:
        return None
    result = row.result
    if lang == "ar":
        sentence = f"نتيجة الفحص التأكيدي {code} (رقم {row.id}) هي {result}، وسُجِّلت في {when}."
        field = "الفحص التأكيدي"
    else:
        sentence = f"Confirmatory test {code} (#{row.id}) result is {result}, recorded on {when}."
        field = "Confirmatory test"
    src = flatten(f"#{row.id} · {code} · {result} · {when}")
    values = {"id": row.id, "code": code, "result": result,
              "date_utc": row.decided_at or row.created_at, "date_display": when}
    return Fact("negative_confirmatory_result", values, field, src, _href(case.id, "case-timeline"), sentence)


def _milestone_notified(db: Session, case: Case, lang: str) -> Fact | None:
    comm = db.scalar(select(Communication).where(
        Communication.case_id == case.id, Communication.category == "result_notification",
        Communication.status == "completed").order_by(Communication.sent_at.asc(), Communication.id.asc()).limit(1))
    when_utc, origin, oid = (None, None, None)
    if comm is not None and comm.sent_at:
        when_utc, origin, oid = comm.sent_at, "communications.sent_at", comm.id
    else:
        step = db.scalar(select(StepTask).where(
            StepTask.case_id == case.id, StepTask.state == S.PATIENT_NOTIFIED)
            .order_by(StepTask.started_at.asc(), StepTask.id.asc()).limit(1))
        if step is not None and step.started_at:
            when_utc, origin, oid = step.started_at, "step_tasks.started_at", step.id
    if not when_utc:
        return None
    when = format_dt(when_utc, lang)
    if lang == "ar":
        sentence, field = f"أُبلِغ المريض بالنتيجة في {when}.", "التبليغ بالنتيجة"
    else:
        sentence, field = f"The patient was notified of the result on {when}.", "Patient notified"
    return Fact("milestone_notified",
                {"date_utc": when_utc, "date_display": when, "origin": origin, "id": oid},
                field, when, _href(case.id, "case-timeline"), sentence)


def _milestone_teachback(db: Session, case: Case, lang: str) -> Fact | None:
    rows = list(db.scalars(select(QuestionnaireResponse).where(
        QuestionnaireResponse.case_id == case.id)
        .order_by(QuestionnaireResponse.created_at.asc(), QuestionnaireResponse.id.asc())))
    if not rows:
        return None
    passed = next((r for r in rows if r.passed), None)
    row = passed or rows[-1]
    if not row.created_at:
        return None
    when = format_dt(row.created_at, lang)
    values = {"id": row.id, "date_utc": row.created_at, "date_display": when,
              "score": row.score, "total": row.total, "passed": row.passed}
    if row.passed:
        if lang == "ar":
            sentence = f"اكتمل التحقق من الفهم (teach-back) في {when} (الدرجة {row.score}/{row.total})."
            field = "التحقق من الفهم"
        else:
            sentence = f"Teach-back was completed on {when} (score {row.score}/{row.total})."
            field = "Teach-back"
    else:
        if lang == "ar":
            sentence = (f"سُجِّل التحقق من الفهم (teach-back) في {when} "
                        f"ولم يُجتَز بعد (الدرجة {row.score}/{row.total}).")
            field = "التحقق من الفهم"
        else:
            sentence = (f"Teach-back was recorded on {when} and had not yet passed "
                        f"(score {row.score}/{row.total}).")
            field = "Teach-back"
    src = f"{when} · {row.score}/{row.total} · {'passed' if row.passed else 'not passed'}"
    return Fact("milestone_teachback", values, field, src, _href(case.id, "case-timeline"), sentence)


def _milestone_referral(db: Session, case: Case, lang: str, *, accepted: bool) -> Fact | None:
    q = select(ServiceRequest).where(ServiceRequest.case_id == case.id, ServiceRequest.kind == "referral")
    if accepted:
        q = q.where(ServiceRequest.status == "accepted")
        row = db.scalar(q.order_by(ServiceRequest.id.desc()).limit(1))
        when_utc = (row.decided_at or row.created_at) if row else None
        key = "milestone_referral_accepted"
    else:
        row = db.scalar(q.order_by(ServiceRequest.created_at.asc(), ServiceRequest.id.asc()).limit(1))
        when_utc = row.created_at if row else None
        key = "milestone_referral_sent"
    if row is None or not when_utc:
        return None
    when = format_dt(when_utc, lang)
    code = (row.code or "").strip() or None
    if accepted:
        if lang == "ar":
            extra = f" ({code})" if code else ""
            sentence, field = f"قُبل التحويل{extra} في {when}.", "قبول التحويل"
        else:
            extra = f" ({code})" if code else ""
            sentence, field = f"Referral{extra} was accepted on {when}.", "Referral accepted"
    else:
        if lang == "ar":
            extra = f" ({code})" if code else ""
            sentence, field = f"أُرسل التحويل{extra} في {when}.", "إرسال التحويل"
        else:
            extra = f" ({code})" if code else ""
            sentence, field = f"Referral{extra} was sent on {when}.", "Referral sent"
    values = {"id": row.id, "date_utc": when_utc, "date_display": when, "code": code, "status": row.status}
    src = flatten(" · ".join(b for b in (code, row.status, when) if b))
    return Fact(key, values, field, src, _href(case.id, "case-timeline"), sentence)


def _milestone_attended(db: Session, case: Case, lang: str, *, skip_id: int | None) -> Fact | None:
    appt = wf._latest_this_episode(db, Appointment, case, status="fulfilled")
    if appt is None or (skip_id is not None and appt.id == skip_id):
        return None
    start = format_dt(appt.start, lang) if appt.start else None
    if not start:
        return None
    attended = format_dt(appt.updated_at, lang) if appt.updated_at else None
    att = (f" on {attended}" if lang == "en" else f" في {attended}") if attended else ""
    if lang == "ar":
        sentence = f"الموعد المجدول في {start} سُجِّل كحضور{att}."
        field = "حضور الموعد"
    else:
        sentence = f"The appointment scheduled for {start} was recorded as attended{att}."
        field = "Appointment attended"
    values = {"id": appt.id, "start_utc": appt.start, "start_display": start,
              "attended_utc": appt.updated_at, "attended_display": attended}
    src = flatten(" · ".join(b for b in (start, attended) if b))
    return Fact("milestone_appointment_attended", values, field, src, _href(case.id, "case-timeline"), sentence)


def _milestone_treatment(db: Session, case: Case, lang: str, *, skip_id: int | None) -> Fact | None:
    plan = wf._latest_this_episode(db, CarePlan, case)
    if plan is None or (skip_id is not None and plan.id == skip_id) or not plan.created_at:
        return None
    when = format_dt(plan.created_at, lang)
    kind = plan.kind if plan.kind in ("treatment", "surveillance") else None
    if kind == "surveillance":
        sentence = (f"Surveillance started on {when}." if lang == "en"
                    else f"بدأت المتابعة في {when}.")
        field = "Surveillance started" if lang == "en" else "بدء المتابعة"
    elif kind == "treatment":
        sentence = (f"Treatment started on {when}." if lang == "en"
                    else f"بدأ العلاج في {when}.")
        field = "Treatment started" if lang == "en" else "بدء العلاج"
    else:
        return None
    values = {"id": plan.id, "kind": plan.kind, "date_utc": plan.created_at, "date_display": when}
    return Fact("milestone_treatment_started", values, field, when, _href(case.id, "case-timeline"), sentence)


def _owner_fact(case: Case, lang: str) -> Fact | None:
    owner = case.owner
    if owner is None or not (owner.name or "").strip():
        return None
    name = owner.name.strip()
    if lang == "ar":
        sentence, field = f"المسؤول الحالي: {name}.", "المسؤول الحالي"
    else:
        sentence, field = f"Current owner: {name}.", "Current owner"
    return Fact("owner", {"name": name, "id": owner.id, "is_pool": bool(owner.is_pool), "role": owner.role},
                field, name, _href(case.id, "act-h"), sentence)


def _contact_fact(db: Session, case: Case, lang: str, *, wanted: bool) -> Fact | None:
    if not wanted:
        return None
    rows = list(db.scalars(select(CallTask).where(
        CallTask.case_id == case.id, CallTask.status == "completed", CallTask.outcome != "reached")
        .order_by(CallTask.completed_at.asc().nulls_last(), CallTask.id.asc())))
    if not rows:
        return None
    last = rows[-1]
    when_utc = last.completed_at or last.created_at
    if not when_utc:
        return None
    when = format_dt(when_utc, lang)
    n, outcome = len(rows), last.outcome
    if lang == "ar":
        sentence = (f"محاولات الاتصال الهاتفي الفاشلة في السجل: {n}؛ "
                    f"آخر محاولة مكتملة في {when} (النتيجة {outcome}).")
        field = "محاولات التواصل"
    else:
        sentence = (f"Failed phone attempts on record: {n}; "
                    f"last completed attempt on {when} (outcome {outcome}).")
        field = "Contact attempts"
    src = f"{n} · {outcome} · {when}"
    values = {"n": n, "outcome": outcome, "date_utc": when_utc, "date_display": when, "id": last.id}
    return Fact("contact_attempts", values, field, src, _href(case.id, "case-timeline"), sentence)


def _step_fact(case: Case, lang: str) -> Fact | None:
    label = STATE_LABEL[lang].get(case.state) or STATE_LABEL["en"].get(case.state)
    if not label or not case.state_entered_at:
        return None
    when = format_dt(case.state_entered_at, lang)
    if lang == "ar":
        sentence, field = f"الخطوة الحالية: {label}، بدأت في {when}.", "الخطوة الحالية"
    else:
        sentence, field = f"Current step: {label}, entered on {when}.", "Current step"
    values = {"state": case.state, "label": label, "date_utc": case.state_entered_at, "date_display": when}
    return Fact("current_step", values, field, f"{label} · {when}", _href(case.id, "nb-h"), sentence)


def _due_fact(case: Case, lang: str) -> Fact | None:
    if not case.due_at:
        return None
    when = format_dt(case.due_at, lang)
    if lang == "ar":
        sentence, field = f"استحقاق الخطوة في {when}.", "الاستحقاق"
    else:
        sentence, field = f"Step due: {when}.", "Step due"
    return Fact("step_due", {"date_utc": case.due_at, "date_display": when},
                field, when, _href(case.id, "nb-h"), sentence)


def _escalation_fact(case: Case, lang: str) -> Fact | None:
    n = case.escalation_level or 0
    if n <= 0:
        return None
    if lang == "ar":
        sentence, field = f"مستوى التصعيد في السجل: {n}.", "التصعيد"
    else:
        sentence, field = f"Escalation level on record: {n}.", "Escalation level"
    return Fact("escalation", {"n": n}, field, str(n), _href(case.id, "nb-h"), sentence)


# --------------------------------------------------------------------------- evidence lookup (same rows the closure guard uses)

def _evidence_row(db: Session, case: Case, evidence_type: str):
    try:
        eid = wf._verify_evidence(db, case, evidence_type, None)
    except Exception:  # TransitionError or missing: omit, never guess
        return None
    model = {"care_plan": CarePlan, "appointment_fulfilled": Appointment,
             "negative_confirmatory_result": ServiceRequest}[evidence_type]
    return db.get(model, eid)


def _pname(db: Session, pid: int | None) -> str | None:
    if not pid:
        return None
    p = db.get(Practitioner, pid)
    name = (p.name or "").strip() if p is not None else ""
    return name or None


def _oname(db: Session, oid: int | None) -> str | None:
    if not oid:
        return None
    o = db.get(Organization, oid)
    name = (o.name or "").strip() if o is not None else ""
    return name or None


def _ensure_report(db: Session, case: Case) -> None:
    """Load the screening report and its observations when the caller did not."""
    if case.report is not None:
        # observations may be lazy
        _ = list(case.report.observations or [])
        return
    if case.report_id:
        rep = db.get(DiagnosticReport, case.report_id)
        if rep is not None:
            _ = list(rep.observations or [])
            case.report = rep


def _ensure_owner(db: Session, case: Case) -> None:
    if case.owner is None and case.owner_id:
        case.owner = db.get(Practitioner, case.owner_id)


# --------------------------------------------------------------------------- compose

def collect_facts(db: Session, case: Case, *, action: str, evidence_type: str | None = None,
                  option: str | None = None, lang: str = "en") -> list[Fact]:
    """Return the ordered list of facts that apply. Missing facts are simply absent."""
    lang = _lang(lang)
    _ensure_report(db, case)
    _ensure_owner(db, case)
    facts: list[Fact] = []

    ev_type = evidence_type if evidence_type in EVIDENCE_TYPES else None
    ev_row = _evidence_row(db, case, ev_type) if ev_type else None
    ev_ok = ev_row is not None

    def add(f: Fact | None) -> None:
        if f is not None:
            facts.append(f)

    add(_closure_fact(case, action=action, evidence_type=ev_type, option=option, lang=lang, evidence_ok=ev_ok))
    skip_plan = skip_appt = None
    if action == "close_evidence" and ev_ok:
        if ev_type == "care_plan":
            add(_care_plan_fact(db, case, lang, required=True))
            skip_plan = ev_row.id if ev_row is not None else None
        elif ev_type == "appointment_fulfilled":
            add(_appointment_evidence_fact(db, case, lang))
            skip_appt = ev_row.id if ev_row is not None else None
        elif ev_type == "negative_confirmatory_result":
            add(_confirmatory_fact(db, case, lang))
    elif ev_type is None:
        # authorised / unreachable / lesson: still surface linked evidence if it is on the record
        add(_care_plan_fact(db, case, lang, required=False))
        if facts and facts[-1].key == "care_plan":
            skip_plan = facts[-1].values.get("id")
        add(_appointment_evidence_fact(db, case, lang))
        if facts and facts[-1].key == "appointment_fulfilled":
            skip_appt = facts[-1].values.get("id")
        add(_confirmatory_fact(db, case, lang))

    add(_programme_fact(case, lang))
    add(_result_fact(case, lang))
    add(_milestone_notified(db, case, lang))
    add(_milestone_teachback(db, case, lang))
    add(_milestone_referral(db, case, lang, accepted=False))
    add(_milestone_referral(db, case, lang, accepted=True))
    add(_milestone_attended(db, case, lang, skip_id=skip_appt))
    add(_milestone_treatment(db, case, lang, skip_id=skip_plan))
    add(_owner_fact(case, lang))
    want_contact = action in ("unreachable",) or (action == "close_authorised" and option == "unable_to_contact")
    add(_contact_fact(db, case, lang, wanted=want_contact))
    if action == "lesson":
        add(_step_fact(case, lang))
        add(_due_fact(case, lang))
        add(_escalation_fact(case, lang))
    return facts


def render(facts: list[Fact], *, limit: int = MAX_NOTE) -> str:
    """Join fact sentences, dropping trailing ones only if the 2000-character form cap would be exceeded."""
    lines: list[str] = []
    used = 0
    for f in facts:
        add = len(f.sentence) + (1 if lines else 0)
        if used + add > limit:
            break
        lines.append(f.sentence)
        used += add
    return "\n".join(lines)


def suggest(db: Session, case: Case, *, action: str, evidence_type: str | None = None,
            option: str | None = None, lang: str = "en") -> Suggestion:
    """Build a record-sourced note. Pure: no writes, no audit (the API records those)."""
    lang = _lang(lang)
    facts = collect_facts(db, case, action=action, evidence_type=evidence_type, option=option, lang=lang)
    # If render drops a trailing sentence for length, the returned facts must match the note
    note = render(facts)
    used = [f for f, line in zip(facts, note.split("\n") if note else []) if f.sentence == line]
    return Suggestion(note=note, facts=tuple(used), disclaimer=DISCLAIMER[lang], kind=KIND,
                      fingerprint=fingerprint(note, [f.key for f in used]), lang=lang)


def load_case(db: Session, case_id: int) -> Case | None:
    return db.scalar(select(Case).options(
        joinedload(Case.patient), joinedload(Case.owner),
        joinedload(Case.report).joinedload(DiagnosticReport.observations),
    ).where(Case.id == case_id))


# Numbers that may legally appear in a sentence, for the faithfulness test.
_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def numeric_tokens(text: str) -> list[str]:
    return _NUM.findall(text)


def allowed_numeric_tokens(fact: Fact) -> set[str]:
    """Every number the sentence is allowed to contain, drawn from this fact's stored values."""
    out: set[str] = set()

    def walk(v: Any) -> None:
        if v is None or isinstance(v, bool):
            return
        if isinstance(v, int) and not isinstance(v, bool):
            out.add(str(v))
            out.add(format_number(v))
        elif isinstance(v, float):
            out.add(format_number(v))
            if v == int(v) and abs(v) < 10**12:
                out.add(str(int(v)))
        elif isinstance(v, datetime):
            # both language renderings, so a date's day/year/hour/minute are allowed
            out.update(numeric_tokens(format_dt(v, "en")))
            out.update(numeric_tokens(format_dt(v, "ar")))
        elif isinstance(v, date):
            out.update(numeric_tokens(format_date(v, "en")))
            out.update(numeric_tokens(format_date(v, "ar")))
        elif isinstance(v, str):
            out.update(numeric_tokens(v))
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)

    walk(fact.values)
    return out
