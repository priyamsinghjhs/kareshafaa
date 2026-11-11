"""Rich appointment and result emails: gather facts, render plain + HTML, send safely.

Used by comms/service.py (staff booking, notify, reminders) and by services/patient_requests.py
(coordinator confirms a patient's request). A mail failure is never raised to the clinical write.
"""
from __future__ import annotations

import html as html_lib
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..calendar_uae import to_local
from ..config import get_settings
from ..models import (Appointment, Case, Communication, DiagnosticReport, Observation, Organization,
                      Patient, PatientRequest, Practitioner, ServiceRequest)
from .event_text import (ALARM_WORDS, DATE_FMT, MONTHS, NEGATIVE_MEANING, RANGE, ROLES, SPECIALTIES,
                         WEEKDAYS, is_rtl, texts)
from .content import programme_text

log = logging.getLogger("kareshafaa.mail")

LOGO_PATH = "/static/brand/logo-horizontal-email.png"
APPT_CATEGORIES = frozenset({
    "appointment_confirmation", "appointment_changed", "appointment_cancelled", "appointment_reminder",
})
RESULT_CATEGORIES = frozenset({"result_notification"})
RICH_CATEGORIES = APPT_CATEGORIES | RESULT_CATEGORIES

# Interpretation / conclusion values that mean "usual range" for the patient-facing flag.
_USUAL = frozenset({"n", "negative", "normal"})
_BANNED = ("demo", "prototype", "prototyp", "sample only", "not a real")


def format_uae(utc_naive: datetime | None, lang: str) -> str:
    """Patient-facing date/time in Asia/Dubai, with an explicit UAE-time label."""
    if utc_naive is None:
        return texts(lang)["not_recorded"]
    local = to_local(utc_naive)
    t = texts(lang)
    lang = lang if lang in WEEKDAYS else "en"
    return DATE_FMT[lang].format(
        weekday=WEEKDAYS[lang][local.weekday()],
        day=local.day,
        month=MONTHS[lang][local.month - 1],
        year=local.year,
        time=local.strftime("%H:%M"),
        tz=t["tz_note"],
    )


def logo_url() -> str | None:
    base = (get_settings().public_base_url or "").rstrip("/")
    return f"{base}{LOGO_PATH}" if base else None


def app_url() -> str | None:
    base = (get_settings().public_base_url or "").rstrip("/")
    return f"{base}/patient" if base else None


def _esc(v: Any) -> str:
    return html_lib.escape("" if v is None else str(v), quote=True)


def _role_label(lang: str, role: str | None, specialty: str | None) -> str:
    t = texts(lang)
    roles = ROLES.get(lang, ROLES["en"])
    key = (role or "").strip().lower()
    label = roles.get(key) or (role or "").replace("_", " ").strip()
    specs = SPECIALTIES.get(lang, SPECIALTIES["en"])
    sk = (specialty or "").strip().lower()
    spec = specs.get(sk) or ((specialty or "").replace("_", " ").strip())
    if label and spec and spec.lower() not in label.lower():
        return f"{label} ({spec})"
    return label


def _clinician_line(lang: str, prac: Practitioner | None) -> str | None:
    if prac is None:
        return None
    if getattr(prac, "is_pool", False):
        return ROLES.get(lang, ROLES["en"]).get("team") or "the clinic team"
    name = (prac.name or "").strip()
    if not name:
        return None
    role = _role_label(lang, prac.role, prac.specialty)
    return f"{name}, {role}" if role else name


def _place_line(org: Organization | None) -> tuple[str | None, str | None, str | None]:
    """(name, address_if_any, phone_if_any). Address and phone are omitted unless the record has them."""
    if org is None:
        return None, None, None
    name = (org.name or "").strip() or None
    region = (org.region or "").strip() or None
    # Organization has no address or phone columns; region is the only location fact we can show.
    address = region
    phone = getattr(org, "phone", None)
    phone = (phone or "").strip() or None
    address_extra = getattr(org, "address_line", None) or getattr(org, "address", None)
    address_extra = (address_extra or "").strip() or None
    if address_extra and region:
        address = f"{address_extra}, {region}"
    elif address_extra:
        address = address_extra
    return name, address, phone


def _format_value(obs: Observation | None) -> str | None:
    if obs is None:
        return None
    if obs.value is not None:
        v = obs.value
        num = str(int(v)) if float(v).is_integer() else f"{v:.2f}".rstrip("0").rstrip(".")
        unit = (obs.unit or "").strip()
        return f"{num} {unit}".strip()
    text = (obs.value_text or "").strip()
    if not text:
        return None
    # Cervical co-tests are stored as "HPV|cytology"; never dump the raw pipe to the patient.
    if "|" in text:
        parts = [p.strip() for p in text.split("|") if p.strip()]
        if len(parts) == 2:
            return f"HPV: {parts[0]}; cytology: {parts[1]}"
        return "; ".join(parts)
    return text


def _range_for(lang: str, programme: str, obs: Observation | None) -> str | None:
    unit = ((obs.unit if obs is not None else None) or "").strip().lower().replace("µ", "u")
    display = ((obs.display if obs is not None else None) or "").lower()
    t = RANGE.get(lang, RANGE["en"])
    if programme == "colorectal_fit":
        return t["fit"]
    if programme == "diabetes_hba1c":
        if "mmol/mol" in unit:
            return t["hba1c_mmol"]
        if "mg/dl" in unit or "mg/dl" in display or "glucose" in display or "fpg" in display:
            return t["fpg_mgdl"]
        if "mmol/l" in unit and "mol" not in unit.replace("mmol/l", ""):
            return t["fpg_mmoll"]
        if "mmol/l" in unit:
            return t["fpg_mmoll"]
        return t["hba1c_%"]
    return None  # qualitative programmes: no numeric range


def _flag_kind(case: Case | None, report: DiagnosticReport | None, obs: Observation | None) -> str:
    if case is not None and (case.tier or "") == "critical":
        return "critical"
    conc = ((report.conclusion if report is not None else "") or "").strip().lower()
    interp = ((obs.interpretation if obs is not None else "") or "").strip().lower()
    if conc in _USUAL or interp in _USUAL:
        return "normal"
    return "actionable"


def event_key(*, category: str, appointment_id: int | None = None, report_id: int | None = None,
              request_id: int | None = None) -> str:
    if report_id is not None:
        return f"{category}:report:{report_id}"
    if appointment_id is not None:
        return f"{category}:appt:{appointment_id}"
    if request_id is not None:
        return f"{category}:request:{request_id}"
    return category


def already_sent(db: Session, patient_id: int, key: str) -> bool:
    return db.scalar(select(Communication.id).where(
        Communication.patient_id == patient_id, Communication.channel == "email",
        Communication.status == "completed", Communication.template_key == key).limit(1)) is not None


def _appointment_for(db: Session, case: Case, category: str) -> Appointment | None:
    rows = db.scalars(select(Appointment).where(Appointment.case_id == case.id).order_by(Appointment.id.desc())).all()
    if category == "appointment_cancelled":
        return next((a for a in rows if a.status == "cancelled"), rows[0] if rows else None)
    return next((a for a in rows if a.status == "booked"), rows[0] if rows else None)


def _report_for(db: Session, case: Case) -> DiagnosticReport | None:
    latest = db.scalar(select(DiagnosticReport).where(
        DiagnosticReport.case_id == case.id,
        DiagnosticReport.status.notin_(("entered-in-error", "cancelled")),
    ).order_by(DiagnosticReport.issued_at.desc(), DiagnosticReport.id.desc()).limit(1))
    if latest is not None:
        return latest
    if case.report_id:
        return db.get(DiagnosticReport, case.report_id) or getattr(case, "report", None)
    return getattr(case, "report", None)


def appointment_context(db: Session, case: Case, category: str,
                        appointment: Appointment | None = None) -> dict[str, Any]:
    lang_fallback = "en"
    appt = appointment or _appointment_for(db, case, category)
    org = db.get(Organization, appt.org_id) if appt is not None else None
    prac = db.get(Practitioner, appt.practitioner_id) if appt is not None and appt.practitioner_id else None
    place, address, phone = _place_line(org)
    service = None
    department = None
    if appt is not None and appt.service_request_id:
        sr = db.get(ServiceRequest, appt.service_request_id)
        if sr is not None:
            service = (sr.code or "").replace("_", " ").strip() or None
            department = service
    previous = None
    if appt is not None and appt.rescheduled_from_id:
        prev = db.get(Appointment, appt.rescheduled_from_id)
        if prev is not None:
            previous = prev.start
    return {
        "category": category,
        "patient": case.patient,
        "programme": case.programme,
        "case_id": case.id,
        "request_id": None,
        "appointment_id": appt.id if appt else None,
        "start": appt.start if appt else None,
        "previous_start": previous,
        "clinician": prac,
        "place": place,
        "address": address,
        "phone": phone,
        "service": service,
        "department": department,
        "bring": None,  # no signed-off medical prep is stored; a generic ID reminder is added at render
        "lang_default": (case.patient.language if case.patient is not None else lang_fallback),
    }


def appointment_context_from_request(db: Session, req: PatientRequest, patient: Patient) -> dict[str, Any]:
    org = db.get(Organization, req.confirmed_org_id) if req.confirmed_org_id else None
    place, address, phone = _place_line(org)
    appt = db.get(Appointment, req.appointment_id) if req.appointment_id else None
    prac = None
    if appt is not None and appt.practitioner_id:
        prac = db.get(Practitioner, appt.practitioner_id)
    return {
        "category": "appointment_confirmation",
        "patient": patient,
        "programme": req.programme,
        "case_id": req.case_id,
        "request_id": req.id,
        "appointment_id": req.appointment_id,
        "start": req.confirmed_start or (appt.start if appt else None),
        "previous_start": None,
        "clinician": prac,
        "place": place,
        "address": address,
        "phone": phone,
        "service": None,
        "department": None,
        "bring": None,
        "lang_default": req.language or patient.language,
    }


def result_context(db: Session, *, case: Case | None = None, report: DiagnosticReport | None = None,
                   patient: Patient | None = None) -> dict[str, Any]:
    if report is None and case is not None:
        report = _report_for(db, case)
    if patient is None:
        if case is not None and case.patient is not None:
            patient = case.patient
        elif report is not None:
            patient = db.get(Patient, report.patient_id)
    obs = None
    if report is not None:
        obs_list = list(report.observations) if report.observations is not None else []
        if not obs_list:
            obs_list = list(db.scalars(select(Observation).where(Observation.report_id == report.id)
                                       .order_by(Observation.id)).all())
        obs = obs_list[0] if obs_list else None
    lab = None
    if report is not None and report.performer_org_id:
        org = db.get(Organization, report.performer_org_id)
        lab = org.name if org is not None else None
        if report.screening_site and (not lab or report.screening_site.strip() != lab):
            lab = report.screening_site if not lab else f"{lab} ({report.screening_site})"
    owner = None
    if case is not None and case.owner_id:
        owner = db.get(Practitioner, case.owner_id)
        if owner is not None and owner.is_pool:
            owner = None
    programme = (case.programme if case is not None else None) or (report.programme if report else None)
    return {
        "category": "result_notification",
        "patient": patient,
        "programme": programme,
        "case": case,
        "case_id": case.id if case is not None else None,
        "report": report,
        "report_id": report.id if report is not None else None,
        "obs": obs,
        "lab": lab,
        "ordered_by": (report.ordering_clinician if report is not None else None),
        "collected_at": (report.specimen_collected_at if report is not None else None),
        "issued_at": (report.issued_at if report is not None else None),
        "owner": owner,
        "lang_default": patient.language if patient is not None else "en",
        "conclusion": (report.conclusion if report is not None else None),
    }


def _programme_name(lang: str, programme: str, case: Case | None = None) -> str:
    if case is not None:
        try:
            return programme_text(lang, case)["name"]
        except Exception:  # noqa: BLE001 - fall back to static names
            pass
    from .content import PROGRAMME_TEXT  # noqa: PLC0415

    pack = PROGRAMME_TEXT.get(lang) or PROGRAMME_TEXT["en"]
    row = pack.get(programme) or PROGRAMME_TEXT["en"].get(programme) or {}
    return row.get("name") or programme.replace("_", " ")


def _meaning_and_next(lang: str, ctx: dict) -> tuple[str, str]:
    t = texts(lang)
    flag = _flag_kind(ctx.get("case"), ctx.get("report"), ctx.get("obs"))
    case = ctx.get("case")
    programme = ctx.get("programme") or ""
    if flag == "normal" or (ctx.get("conclusion") or "").lower() in _USUAL:
        meaning = (NEGATIVE_MEANING.get(lang) or NEGATIVE_MEANING["en"]).get(programme) \
            or (NEGATIVE_MEANING["en"].get(programme) or t["lead"]["normal"])
        return meaning, t["next_normal"]
    meaning, next_step = "", t["next_help"]
    if case is not None:
        try:
            prog = programme_text(lang, case)
            meaning, next_step = prog.get("meaning") or "", prog.get("next_step") or next_step
        except Exception:  # noqa: BLE001
            pass
    if not meaning:
        from .content import PROGRAMME_TEXT  # noqa: PLC0415

        pack = (PROGRAMME_TEXT.get(lang) or PROGRAMME_TEXT["en"]).get(programme) or {}
        meaning = pack.get("meaning") or ""
        next_step = pack.get("next_step") or next_step
    from .content import TIMEFRAME  # noqa: PLC0415

    if case is not None and case.tier and not (ctx.get("conclusion") or "").lower() in _USUAL:
        when = (TIMEFRAME.get(lang) or TIMEFRAME["en"]).get(case.tier)
        if when and next_step and when not in next_step:
            sep = "، " if lang in ("ar", "ur") else ", "
            next_step = f"{next_step}{sep}{when}"
    if next_step and t["next_help"] not in next_step:
        next_step = f"{next_step}. {t['next_help']}"
    return meaning, next_step


def _who_line(lang: str, ctx: dict) -> str:
    t = texts(lang)
    owner = ctx.get("owner")
    line = _clinician_line(lang, owner) if owner is not None else None
    return line or t["who_team"]


def key_for_context(ctx: dict) -> str:
    return event_key(category=ctx["category"], appointment_id=ctx.get("appointment_id"),
                     report_id=ctx.get("report_id"), request_id=ctx.get("request_id"))


def _assert_safe(text: str) -> str:
    low = text.lower()
    for w in _BANNED:
        if w in low:
            raise ValueError(f"patient email would contain banned wording: {w}")
    return text


def render_appointment(lang: str, ctx: dict) -> tuple[str, str, str]:
    t = texts(lang)
    cat = ctx["category"]
    patient = ctx["patient"]
    name = (patient.given_name if patient is not None else "") or ""
    subject = t["subjects"][cat]
    greeting = t["hello"].format(name=name)
    intro = t["intro"][cat]
    rows: list[tuple[str, str]] = []
    labels = t["labels"]
    rows.append((labels["programme"], _programme_name(lang, ctx.get("programme") or "", ctx.get("case"))))
    if ctx.get("service"):
        rows.append((labels["service"], ctx["service"]))
    if ctx.get("start") is not None:
        rows.append((labels["when"], format_uae(ctx["start"], lang)))
    if ctx.get("previous_start") is not None and cat == "appointment_changed":
        rows.append((labels["previous"], format_uae(ctx["previous_start"], lang)))
    clin = _clinician_line(lang, ctx.get("clinician"))
    if clin:
        rows.append((labels["clinician"], clin))
    if ctx.get("place"):
        rows.append((labels["place"], ctx["place"]))
    if ctx.get("address"):
        rows.append((labels["address"], ctx["address"]))
    if ctx.get("phone"):
        rows.append((labels["phone"], ctx["phone"]))
    if ctx.get("department") and ctx.get("department") != ctx.get("service"):
        rows.append((labels["department"], ctx["department"]))
    rows.append((labels["bring"], ctx.get("bring") or t["bring_default"]))
    if cat != "appointment_cancelled":
        rows.append((labels["change"], t["change_how"]))
    else:
        rows.append((labels["change"], t["change_how"]))
    if ctx.get("case_id"):
        rows.append((labels["reference"], t["case_ref"].format(id=ctx["case_id"])))
    elif ctx.get("request_id"):
        rows.append((labels["reference"], t["request_ref"].format(id=ctx["request_id"])))
    plain = _plain(greeting, intro, rows, None, t)
    html = _html(lang, subject, greeting, intro, rows, None, t)
    return _assert_safe(subject), _assert_safe(plain), html


def render_result(lang: str, ctx: dict) -> tuple[str, str, str]:
    t = texts(lang)
    patient = ctx["patient"]
    name = (patient.given_name if patient is not None else "") or ""
    subject = t["subjects"]["result_notification"]
    greeting = t["hello"].format(name=name)
    flag = _flag_kind(ctx.get("case"), ctx.get("report"), ctx.get("obs"))
    intro = t["lead"][flag]
    meaning, next_step = _meaning_and_next(lang, ctx)
    labels = t["labels"]
    rows: list[tuple[str, str]] = []
    rows.append((labels["programme"], _programme_name(lang, ctx.get("programme") or "", ctx.get("case"))))
    obs = ctx.get("obs")
    report = ctx.get("report")
    test_name = None
    if obs is not None and (obs.display or "").strip():
        test_name = obs.display.strip()
    elif report is not None and (report.display or "").strip():
        test_name = report.display.strip()
    if test_name:
        rows.append((labels["test"], test_name))
    if ctx.get("collected_at") is not None:
        rows.append((labels["collected"], format_uae(ctx["collected_at"], lang)))
    if ctx.get("issued_at") is not None:
        rows.append((labels["reported"], format_uae(ctx["issued_at"], lang)))
    value = _format_value(obs)
    if value:
        rows.append((labels["value"], value))
    rng = _range_for(lang, ctx.get("programme") or "", obs)
    if rng:
        rows.append((labels["range"], rng))
    flag_text = t["flag"][flag]
    if meaning and meaning not in flag_text:
        end = flag_text[-1:]
        flag_text = f"{flag_text} {meaning}" if end in ".؟!।" else f"{flag_text}. {meaning}"
    rows.append((labels["flag"], flag_text))
    rows.append((labels["next"], next_step))
    rows.append((labels["who"], _who_line(lang, ctx)))
    if ctx.get("ordered_by"):
        rows.append((labels["ordered_by"], ctx["ordered_by"]))
    if ctx.get("lab"):
        rows.append((labels["lab"], ctx["lab"]))
    if ctx.get("case_id"):
        rows.append((labels["reference"], t["case_ref"].format(id=ctx["case_id"])))
    # Safety: the lead is already the first paragraph. Re-check flag/lead have no alarm words.
    _check_no_alarm(intro + " " + t["flag"][flag], lang)
    plain = _plain(greeting, intro, rows, None, t)
    html = _html(lang, subject, greeting, intro, rows, flag, t, lead_kind=flag)
    return _assert_safe(subject), _assert_safe(plain), html


def _check_no_alarm(text: str, lang: str) -> None:
    """The flag and the lead must not alarm. 'Emergency' is allowed only in the 'you do not need to go' sentence."""
    # We allow the critical lead's "You do not need to go to an emergency department".
    allowed = texts(lang)["lead"]["critical"]
    residual = text.replace(allowed, "")
    low = residual.lower()
    for w in ALARM_WORDS:
        if w.lower() in low:
            # 'emergency' in residual would be a problem; already stripped the allowed sentence
            raise ValueError(f"alarm wording {w!r} in patient-facing lead or flag")


def _plain(greeting: str, intro: str, rows: list[tuple[str, str]], _notice: str | None, t: dict) -> str:
    lines = [greeting, "", intro, ""]
    for label, value in rows:
        lines.append(f"{label}: {value}")
    link = app_url()
    if link:
        lines.extend(["", f"{t['open_app']}: {link}"])
    lines.extend(["", t["footer"]])
    return "\n".join(lines).strip() + "\n"


def _html(lang: str, subject: str, greeting: str, intro: str, rows: list[tuple[str, str]],
          flag: str | None, t: dict, *, lead_kind: str | None = None) -> str:
    rtl = is_rtl(lang)
    direction = "rtl" if rtl else "ltr"
    align = "right" if rtl else "left"
    logo = logo_url()
    if logo:
        brand = (f'<img src="{_esc(logo)}" alt="KareShafaa" width="180" '
                 f'style="display:block;border:0;height:auto;max-width:180px;">')
    else:
        brand = ('<span style="font-family:Segoe UI,Arial,sans-serif;font-weight:800;font-size:22px;'
                 'color:#ffffff;letter-spacing:-0.3px;">KareShafaa</span>')
    lead_bg = {"critical": "#eef6f3", "actionable": "#eef6f3", "normal": "#eef6f3"}.get(lead_kind or "", "#eef6f3")
    lead_html = (
        f'<p style="margin:0 0 20px;padding:12px 14px;background:{lead_bg};border-radius:6px;'
        f'border-{("right" if rtl else "left")}:4px solid #0A6B66;font-size:15px;line-height:1.5;">'
        f"{_esc(intro)}</p>"
    )
    row_html = []
    for label, value in rows:
        row_html.append(
            f'<tr>'
            f'<td style="padding:8px 12px 8px 0;color:#5a6570;font-size:13px;width:38%;vertical-align:top;'
            f'text-align:{align};">{_esc(label)}</td>'
            f'<td style="padding:8px 0;color:#122;font-size:15px;vertical-align:top;text-align:{align};" dir="auto">'
            f"{_esc(value)}</td></tr>"
        )
    link = app_url()
    if link:
        cta = (
            f'<p style="margin:28px 0 0;text-align:{align};">'
            f'<a href="{_esc(link)}" style="display:inline-block;background:#0A6B66;color:#ffffff;'
            f'text-decoration:none;padding:12px 20px;border-radius:6px;font-size:15px;font-weight:600;">'
            f"{_esc(t['open_app'])}</a></p>"
        )
    else:
        cta = ""
    return f"""<!DOCTYPE html>
<html lang="{_esc(lang)}" dir="{direction}">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{_esc(subject)}</title></head>
<body style="margin:0;padding:0;background:#f4f6f8;font-family:Segoe UI,Tahoma,Arial,sans-serif;color:#122;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f4f6f8;">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="600" cellspacing="0" cellpadding="0"
       style="max-width:600px;width:100%;background:#ffffff;border-radius:8px;overflow:hidden;">
<tr><td style="background:#0A2B5E;padding:20px 28px;">{brand}</td></tr>
<tr><td style="height:4px;background:#4FD1A1;font-size:0;line-height:0;">&nbsp;</td></tr>
<tr><td style="padding:28px;" dir="{direction}" align="{align}">
  <p style="margin:0 0 12px;font-size:16px;">{_esc(greeting)}</p>
  {lead_html}
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;">
    {''.join(row_html)}
  </table>
  {cta}
</td></tr>
<tr><td style="padding:16px 28px 24px;color:#5a6570;font-size:12px;line-height:1.5;border-top:1px solid #e6eaee;"
        dir="{direction}" align="{align}">{_esc(t['footer'])}</td></tr>
</table>
</td></tr>
</table>
</body></html>
"""

def render(lang: str, ctx: dict) -> tuple[str, str, str]:
    lang = lang if lang in ("en", "ar", "hi", "ur") else "en"
    if ctx["category"] in APPT_CATEGORIES:
        return render_appointment(lang, ctx)
    return render_result(lang, ctx)


def render_for_case(db: Session, case: Case, category: str, lang: str) -> tuple[str, str, str, str] | None:
    """Returns (subject, plain, html, event_key) or None if this category has no rich template."""
    if category in APPT_CATEGORIES:
        ctx = appointment_context(db, case, category)
        subject, plain, html = render_appointment(lang, ctx)
        return subject, plain, html, key_for_context(ctx)
    if category in RESULT_CATEGORIES:
        ctx = result_context(db, case=case)
        subject, plain, html = render_result(lang, ctx)
        return subject, plain, html, key_for_context(ctx)
    return None


def _can_email_patient(db: Session, patient: Patient, actor=None) -> str | None:
    """Why e-mail must be skipped, or None if it may be sent. Never raises."""
    from ..services.access import consent_status, has_consent  # noqa: PLC0415
    from . import mail  # noqa: PLC0415

    if patient is None:
        return "no_patient"
    if getattr(patient, "deceased", False):
        return "deceased"
    if not mail.configured():
        return "not_configured"
    if not mail.can_send_to_patient(patient):
        return "gated"
    # Automated outreach stops on withdrawal; a clinician may still write (same rule as send_with_ladder).
    if actor is not None and not getattr(actor, "is_human", False)             and consent_status(db, patient.id, "care_coordination") == "deny":
        return "care_coordination_denied"
    if not has_consent(db, patient.id, "comm_email"):
        return "no_consent"
    return None


def send_rendered(db: Session, *, patient: Patient, category: str, lang: str, subject: str, plain: str,
                  html: str, key: str, case_id: int | None, actor=None, now: datetime | None = None) -> dict:
    """Send one already-rendered e-mail, store a Communication, never raise. Idempotent on `key`."""
    from .. import audit  # noqa: PLC0415
    from ..models import utcnow  # noqa: PLC0415
    from ..security import SYSTEM  # noqa: PLC0415
    from . import mail  # noqa: PLC0415

    now = now or utcnow()
    actor = actor or SYSTEM
    out = {"sent": False, "skipped": None, "communication_id": None, "already_sent": False}
    try:
        reason = _can_email_patient(db, patient, actor)
        if reason:
            out["skipped"] = reason
            return out
        if already_sent(db, patient.id, key):
            out["skipped"] = "already_sent"
            out["already_sent"] = True
            out["sent"] = False
            return out
        try:
            mail.send_email(patient.email, subject, plain, html_body=html)
            ok, content, err = True, plain, None
        except mail.MailError as e:
            ok, content, err = False, f"(e-mail not sent: {e})", str(e)
        from sqlalchemy import func  # noqa: PLC0415

        attempt = (db.scalar(select(func.count()).select_from(Communication).where(
            Communication.patient_id == patient.id, Communication.category == category)) or 0) + 1
        com = Communication(case_id=case_id, patient_id=patient.id, category=category, channel="email",
                            language=lang, template_key=key, template_variant="standard", content=content,
                            status="completed" if ok else "failed", simulated=False, attempt_no=attempt,
                            sent_at=now)
        db.add(com)
        db.flush()
        out["communication_id"] = com.id
        out["sent"] = ok
        if not ok:
            out["skipped"] = "mail_error"
            out["error"] = err
        audit.record(db, actor, "C", f"communication.{category}", entity_type="Communication",
                     entity_id=com.id, case_id=case_id, ts=now,
                     detail={"channel": "email", "delivered": ok, "simulated": False, "language": lang,
                             "event_key": key, "already_sent": False, "mail_error": bool(err)})
        return out
    except Exception as e:  # noqa: BLE001 - never fail a clinical write because of mail
        log.warning("event e-mail skipped: %s", type(e).__name__)
        out["skipped"] = type(e).__name__
        return out


def send_for_case(db: Session, case: Case, category: str, lang: str, *, actor=None,
                  now: datetime | None = None) -> dict:
    packed = render_for_case(db, case, category, lang)
    if packed is None:
        return {"sent": False, "skipped": "not_rich"}
    subject, plain, html, key = packed
    return send_rendered(db, patient=case.patient, category=category, lang=lang, subject=subject,
                         plain=plain, html=html, key=key, case_id=case.id, actor=actor, now=now)


def send_for_request(db: Session, req: PatientRequest, patient: Patient, lang: str, *, actor=None,
                     now: datetime | None = None) -> dict:
    ctx = appointment_context_from_request(db, req, patient)
    subject, plain, html = render_appointment(lang, ctx)
    return send_rendered(db, patient=patient, category="appointment_confirmation", lang=lang,
                         subject=subject, plain=plain, html=html, key=key_for_context(ctx),
                         case_id=req.case_id, actor=actor, now=now)


def send_for_result(db: Session, *, case: Case | None = None, report: DiagnosticReport | None = None,
                    patient: Patient | None = None, lang: str | None = None, actor=None,
                    now: datetime | None = None) -> dict:
    ctx = result_context(db, case=case, report=report, patient=patient)
    patient = ctx["patient"]
    lang = lang or (patient.language if patient is not None else "en")
    lang = lang if lang in ("en", "ar", "hi", "ur") else "en"
    subject, plain, html = render_result(lang, ctx)
    return send_rendered(db, patient=patient, category="result_notification", lang=lang,
                         subject=subject, plain=plain, html=html, key=key_for_context(ctx),
                         case_id=ctx.get("case_id"), actor=actor, now=now)
