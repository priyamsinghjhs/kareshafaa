"""Patient account invites: staff create the patient's login from the patient record with a one-time code.

* The account (role patient, linked to the patient record) is created with NO password: nobody can sign in
  until the patient chooses one on /activate with the code, their date of birth and a new password.
* Code: 8 characters (no look-alike letters), shown ONCE to staff and sent in a SIMULATED SMS. Only HMAC hashes
  are stored (code and link token), plus the last 4 characters so staff can tell invites apart.
* Single use, expires after INVITE_TTL_HOURS, revoked by a new invite or by staff, and after
  MAX_FAILED_ATTEMPTS wrong dates of birth. Every step is audited.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import accounts, audit
from ..config import get_settings
from ..models import Communication, LoginAttempt, Patient, PatientInvite, User, utcnow
from ..security import Actor
from .errors import DomainError, Forbidden, Invalid

INVITE_TTL_HOURS = 72          # time to activate (operational setting, not clinical)
MAX_FAILED_ATTEMPTS = 5        # wrong date of birth for one invite -> invite revoked, staff send a new one
IP_MAX_FAILURES = 20           # per IP in the login window (shared with the sign-in rate limit)
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L
ACTIVATION_USER = "~activation"  # LoginAttempt marker for failed activations (rate limiting only)

# SIMULATED SMS (one language per message; the code is masked in what is stored)
SMS_TEXT = {
    "en": "KareShafaa: your patient account is ready. Open {link} and enter activation code {code} and your date of birth. The code works once and expires on {expires}.",
    "ar": "كير شفاء: حسابك جاهز. افتح الرابط {link} وأدخل رمز التفعيل {code} وتاريخ ميلادك. الرمز يعمل مرة واحدة وينتهي في {expires}.",
    "hi": "केयरशफ़ा: आपका मरीज़ खाता तैयार है। {link} खोलें और सक्रियण कोड {code} तथा अपनी जन्मतिथि दर्ज करें। कोड एक बार काम करता है और {expires} को समाप्त होता है।",
    "ur": "کیئر شفاء: آپ کا مریض اکاؤنٹ تیار ہے۔ {link} کھولیں اور ایکٹیویشن کوڈ {code} اور اپنی تاریخ پیدائش درج کریں۔ کوڈ ایک بار کام کرتا ہے اور {expires} کو ختم ہو جاتا ہے۔",
}


class InviteError(DomainError):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def _h(kind: str, value: str) -> str:
    key = (get_settings().secret_key or "").encode()
    return hmac.new(key, f"{kind}|{value}".encode(), hashlib.sha256).hexdigest()


def normalise_code(raw: str | None) -> str:
    return "".join(ch for ch in (raw or "").upper() if ch.isalnum())


def _new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(8))


def fmt_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def _username(db: Session, p: Patient) -> str:
    base = "p." + "".join(ch for ch in (p.mrn or str(p.id)).lower() if ch.isalnum() or ch == "-")[:60]
    u, n = base, 1
    while db.scalar(select(User.id).where(User.username == u)) is not None:
        n += 1
        u = f"{base}.{n}"
    return u


def active_invite(db: Session, patient_id: int, now: datetime | None = None) -> PatientInvite | None:
    now = now or utcnow()
    return db.scalar(select(PatientInvite).where(PatientInvite.patient_id == patient_id, PatientInvite.used_at.is_(None),
                                                 PatientInvite.revoked_at.is_(None), PatientInvite.expires_at > now)
                     .order_by(PatientInvite.id.desc()).limit(1))


def account_status(db: Session, p: Patient, now: datetime | None = None) -> dict:
    now = now or utcnow()
    u = db.scalar(select(User).where(User.patient_id == p.id, User.role == "patient").order_by(User.id).limit(1))
    inv = active_invite(db, p.id, now)
    last = db.scalar(select(PatientInvite).where(PatientInvite.patient_id == p.id).order_by(PatientInvite.id.desc()).limit(1))
    if u is None:
        state = "none"
    elif not u.active:
        state = "inactive"
    elif u.password_hash:
        state = "active"
    else:
        state = "invited" if inv else "invite_expired"
    return {"state": state, "username": u.username if u else None,
            "activated_at": u.password_changed_at.isoformat() if u and u.password_hash and u.password_changed_at else None,
            "last_login_at": u.last_login_at.isoformat() if u and u.last_login_at else None,
            "invite": ({"id": inv.id, "code_hint": "…" + inv.code_last4, "expires_at": inv.expires_at.isoformat(),
                        "created_at": inv.created_at.isoformat(), "created_by": inv.created_by, "channel": inv.channel,
                        "purpose": inv.purpose} if inv else None),
            "last_invite": ({"created_at": last.created_at.isoformat(), "used_at": last.used_at.isoformat() if last.used_at else None,
                             "revoked_at": last.revoked_at.isoformat() if last.revoked_at else None,
                             "revoke_reason": last.revoke_reason, "expires_at": last.expires_at.isoformat()} if last else None)}


@dataclass
class NewInvite:
    invite: PatientInvite
    user: User
    code: str
    link: str
    sms: Communication | None


def create_invite(db: Session, actor: Actor, p: Patient, *, channel: str, base_url: str,
                  now: datetime | None = None) -> NewInvite:
    """Create (or re-use) the patient's account and a fresh one-time code. Earlier open invites are revoked."""
    from .patients import can_write_patient

    now = now or utcnow()
    if not actor.is_human or not actor.can("patient:invite"):
        raise Forbidden("your role cannot create patient accounts (coordinators and GPs do)")
    if not can_write_patient(db, actor, p):
        raise Forbidden("you can only invite patients you care for")
    if p.deceased:
        raise Invalid("this patient is recorded as deceased - no account can be created")
    if channel not in ("sms", "in_person"):
        raise Invalid("channel must be sms or in_person")
    if channel == "sms" and not (p.phone and p.phone_valid):
        raise Invalid("no valid mobile number on the record - add one under Contact details, or give the code in person")
    if p.birth_date is None:
        raise Invalid("add the full date of birth under Personal details first - the patient confirms it when activating")
    u = db.scalar(select(User).where(User.patient_id == p.id, User.role == "patient").order_by(User.id).limit(1))
    created = u is None
    if u is None:
        u = User(username=_username(db, p), display_name=f"{p.given_name} {p.family_name}"[:200], role="patient",
                 patient_id=p.id, active=True, password_hash=None, must_change_password=False, created_at=now)
        db.add(u)
        db.flush()
    elif not u.active:
        raise Invalid("this patient's account is deactivated - an administrator must reactivate it first")
    purpose = "reset" if u.password_hash else "activate"
    for old in db.scalars(select(PatientInvite).where(PatientInvite.patient_id == p.id, PatientInvite.used_at.is_(None),
                                                      PatientInvite.revoked_at.is_(None))):
        old.revoked_at, old.revoked_by, old.revoke_reason = now, actor.display, "replaced by a new code"
    code, token = _new_code(), secrets.token_urlsafe(24)
    inv = PatientInvite(patient_id=p.id, user_id=u.id, code_hash=_h("code", code), link_hash=_h("link", token),
                        code_last4=code[-4:], purpose=purpose, channel=channel, created_at=now, created_by=actor.display,
                        expires_at=now + timedelta(hours=INVITE_TTL_HOURS), failed_attempts=0)
    db.add(inv)
    db.flush()
    link = base_url.rstrip("/") + "/activate?t=" + token
    sms = None
    if channel == "sms":
        from ..comms.service import SIM_PREFIX, comm_language
        from ..calendar_uae import to_local

        lang = comm_language(p)
        lang = lang if lang in SMS_TEXT else "en"
        exp = to_local(inv.expires_at).strftime("%d/%m/%Y %H:%M")
        masked = SMS_TEXT[lang].format(link=base_url.rstrip("/") + "/activate", code="****-" + code[-4:], expires=exp)
        sms = Communication(patient_id=p.id, direction="outbound", category="account_invite", channel="sms",
                            language=lang, template_key="account_invite", template_variant="standard",
                            content=SIM_PREFIX + masked, status="completed", simulated=True, sent_at=now)
        db.add(sms)
        db.flush()
        inv.communication_id = sms.id
    audit.record(db, actor, "C", "patient.account_invited", entity_type="Patient", entity_id=p.id, ts=now,
                 detail={"invite_id": inv.id, "user_id": u.id, "username": u.username, "account_created": created,
                         "purpose": purpose, "channel": channel, "expires_at": inv.expires_at.isoformat()})
    return NewInvite(inv, u, code, link, sms)


def revoke_invite(db: Session, actor: Actor, p: Patient, reason: str, now: datetime | None = None) -> PatientInvite:
    from .patients import can_write_patient

    now = now or utcnow()
    if not actor.is_human or not actor.can("patient:invite") or not can_write_patient(db, actor, p):
        raise Forbidden("your role cannot manage patient accounts")
    inv = active_invite(db, p.id, now)
    if inv is None:
        raise Invalid("there is no open activation code to cancel")
    inv.revoked_at, inv.revoked_by, inv.revoke_reason = now, actor.display, reason.strip()[:300]
    audit.record(db, actor, "U", "patient.account_invite_revoked", entity_type="Patient", entity_id=p.id, ts=now,
                 detail={"invite_id": inv.id, "reason": inv.revoke_reason})
    return inv


def _ip_blocked(db: Session, ip: str | None, now: datetime) -> bool:
    if not ip:
        return False
    s = get_settings()
    since = now - timedelta(minutes=s.login_window_minutes)
    n = db.scalar(select(func.count(LoginAttempt.id)).where(LoginAttempt.ip == ip, LoginAttempt.success.is_(False),
                                                           LoginAttempt.at >= since)) or 0
    return n >= min(IP_MAX_FAILURES, s.login_ip_max_failures)


def find_invite(db: Session, *, code: str | None = None, token: str | None = None) -> PatientInvite | None:
    if token:
        return db.scalar(select(PatientInvite).where(PatientInvite.link_hash == _h("link", token)))
    c = normalise_code(code)
    if len(c) != 8:
        return None
    return db.scalar(select(PatientInvite).where(PatientInvite.code_hash == _h("code", c)).order_by(PatientInvite.id.desc()).limit(1))


def activate(db: Session, *, code: str | None, token: str | None, birth_date: date | None, password: str,
             confirm: str, ip: str | None, now: datetime | None = None) -> tuple[User | None, str]:
    """Returns (user, "ok") or (None, reason). reason in: rate_limited, invalid, expired, used, revoked,
    wrong_dob, locked, mismatch, weak:<problem>. Failed attempts are recorded for rate limiting."""
    now = now or utcnow()
    actor = Actor(user_id=None, role="anonymous", display="patient (activation page)", is_human=True)

    def fail(reason: str, inv: PatientInvite | None = None) -> tuple[None, str]:
        if reason not in ("mismatch",) and not reason.startswith("weak:"):
            db.add(LoginAttempt(username=ACTIVATION_USER, ip=(ip or "")[:64] or None, at=now, success=False))
        audit.record(db, actor, "E", "patient.account_activation_failed", entity_type="Patient",
                     entity_id=inv.patient_id if inv else None, ts=now, outcome="failure",
                     detail={"reason": reason.split(":")[0], "invite_id": inv.id if inv else None, "ip": ip})
        db.commit()
        return None, reason

    if _ip_blocked(db, ip, now):
        return None, "rate_limited"
    inv = find_invite(db, code=code, token=token)
    if inv is None:
        return fail("invalid")
    if inv.used_at is not None:
        return fail("used", inv)
    if inv.revoked_at is not None:
        return fail("locked" if inv.revoke_reason == "too many wrong attempts" else "revoked", inv)
    if inv.expires_at <= now:
        return fail("expired", inv)
    if token and code and normalise_code(code) and _h("code", normalise_code(code)) != inv.code_hash:
        return fail("invalid", inv)
    p = db.get(Patient, inv.patient_id)
    u = db.get(User, inv.user_id)
    if p is None or u is None or not u.active or p.deceased:
        return fail("revoked", inv)
    if birth_date is None or p.birth_date != birth_date:
        inv.failed_attempts = (inv.failed_attempts or 0) + 1
        if inv.failed_attempts >= MAX_FAILED_ATTEMPTS:
            inv.revoked_at, inv.revoked_by, inv.revoke_reason = now, "system", "too many wrong attempts"
            return fail("locked", inv)
        return fail("wrong_dob", inv)
    if password != confirm:
        return None, "mismatch"
    probs = accounts.password_problems(password, u.username, u.display_name)
    if probs:
        return None, "weak:" + probs[0]
    accounts.set_password(u, password, must_change=False)
    accounts.revoke_user_sessions(db, u.id)
    inv.used_at = now
    db.add(LoginAttempt(username=u.username, ip=(ip or "")[:64] or None, at=now, success=True))
    audit.record(db, actor, "U", "patient.account_activated", entity_type="Patient", entity_id=p.id, ts=now,
                 detail={"invite_id": inv.id, "user_id": u.id, "purpose": inv.purpose})
    return u, "ok"


# ------------------------------------------------------------------------------ activation page wording
ACTIVATE_TEXT = {
    "en": {"title": "Activate your patient account", "intro": "Enter the activation code from your text message or from the care team, your date of birth, and choose a password.",
           "code": "Activation code", "code_hint": "8 letters and numbers, for example ABCD-2345", "code_from_link": "The code from your link is filled in for you.",
           "dob": "Your date of birth", "pw": "Choose a password", "pw_hint": "At least 12 characters. Three or four unrelated words are easy to remember.",
           "pw2": "Type the password again", "submit": "Activate my account", "show": "Show", "hide": "Hide",
           "done_title": "Your account is ready", "done_body": "Your username is", "done_keep": "Keep it safe - you will need it with your password to sign in.",
           "go": "Open my page", "have_account": "Already activated?", "sign_in": "Sign in",
           "err": {"invalid": "This activation code is not correct. Check the code and try again.",
                   "expired": "This activation code has expired. Please ask your care team for a new one.",
                   "used": "This activation code has already been used. Sign in with your username and password.",
                   "revoked": "This activation code is no longer valid. Please ask your care team for a new one.",
                   "locked": "Too many wrong attempts. This code is blocked - please ask your care team for a new one.",
                   "wrong_dob": "The date of birth does not match our record. Please check it and try again.",
                   "mismatch": "The two passwords do not match.",
                   "weak": "Please choose a stronger password: at least 12 characters, not a common password, and not your name.",
                   "rate_limited": "Too many attempts from this device. Please wait 15 minutes and try again.",
                   "missing": "Please fill in every field.", "expired_page": "The page expired. Please try again."}},
    "ar": {"title": "تفعيل حسابك", "intro": "أدخل رمز التفعيل من رسالتك النصية أو من فريق الرعاية، وتاريخ ميلادك، ثم اختر كلمة مرور.",
           "code": "رمز التفعيل", "code_hint": "8 أحرف وأرقام، مثل ABCD-2345", "code_from_link": "تمت تعبئة الرمز من الرابط تلقائياً.",
           "dob": "تاريخ ميلادك", "pw": "اختر كلمة مرور", "pw_hint": "12 حرفاً على الأقل. ثلاث أو أربع كلمات غير مترابطة يسهل تذكرها.",
           "pw2": "اكتب كلمة المرور مرة أخرى", "submit": "تفعيل حسابي", "show": "إظهار", "hide": "إخفاء",
           "done_title": "حسابك جاهز", "done_body": "اسم المستخدم الخاص بك هو", "done_keep": "احتفظ به في مكان آمن - ستحتاجه مع كلمة المرور لتسجيل الدخول.",
           "go": "افتح صفحتي", "have_account": "هل فعّلت حسابك من قبل؟", "sign_in": "تسجيل الدخول",
           "err": {"invalid": "رمز التفعيل غير صحيح. تحقق من الرمز وحاول مرة أخرى.",
                   "expired": "انتهت صلاحية رمز التفعيل. يرجى طلب رمز جديد من فريق الرعاية.",
                   "used": "تم استخدام رمز التفعيل من قبل. سجّل الدخول باسم المستخدم وكلمة المرور.",
                   "revoked": "رمز التفعيل لم يعد صالحاً. يرجى طلب رمز جديد من فريق الرعاية.",
                   "locked": "محاولات خاطئة كثيرة. تم إيقاف هذا الرمز - يرجى طلب رمز جديد من فريق الرعاية.",
                   "wrong_dob": "تاريخ الميلاد لا يطابق سجلنا. يرجى التحقق منه والمحاولة مرة أخرى.",
                   "mismatch": "كلمتا المرور غير متطابقتين.",
                   "weak": "يرجى اختيار كلمة مرور أقوى: 12 حرفاً على الأقل، وليست شائعة، ولا تحتوي على اسمك.",
                   "rate_limited": "محاولات كثيرة من هذا الجهاز. يرجى الانتظار 15 دقيقة ثم المحاولة مرة أخرى.",
                   "missing": "يرجى تعبئة جميع الحقول.", "expired_page": "انتهت صلاحية الصفحة. يرجى المحاولة مرة أخرى."}},
    "hi": {"title": "अपना मरीज़ खाता सक्रिय करें", "intro": "अपने टेक्स्ट संदेश या देखभाल टीम से मिला सक्रियण कोड, अपनी जन्मतिथि दर्ज करें और एक पासवर्ड चुनें।",
           "code": "सक्रियण कोड", "code_hint": "8 अक्षर और अंक, जैसे ABCD-2345", "code_from_link": "आपके लिंक से कोड अपने-आप भर दिया गया है।",
           "dob": "आपकी जन्मतिथि", "pw": "पासवर्ड चुनें", "pw_hint": "कम से कम 12 अक्षर। तीन या चार अलग-अलग शब्द याद रखना आसान होता है।",
           "pw2": "पासवर्ड फिर से लिखें", "submit": "मेरा खाता सक्रिय करें", "show": "दिखाएं", "hide": "छिपाएं",
           "done_title": "आपका खाता तैयार है", "done_body": "आपका यूज़रनेम है", "done_keep": "इसे सुरक्षित रखें - साइन इन करने के लिए पासवर्ड के साथ इसकी ज़रूरत होगी।",
           "go": "मेरा पेज खोलें", "have_account": "पहले ही सक्रिय कर चुके हैं?", "sign_in": "साइन इन करें",
           "err": {"invalid": "यह सक्रियण कोड सही नहीं है। कोड जांचें और फिर से कोशिश करें।",
                   "expired": "इस सक्रियण कोड की समय-सीमा खत्म हो गई है। कृपया अपनी देखभाल टीम से नया कोड मांगें।",
                   "used": "यह सक्रियण कोड पहले ही इस्तेमाल हो चुका है। अपने यूज़रनेम और पासवर्ड से साइन इन करें।",
                   "revoked": "यह सक्रियण कोड अब मान्य नहीं है। कृपया अपनी देखभाल टीम से नया कोड मांगें।",
                   "locked": "बहुत सारी गलत कोशिशें। यह कोड रोक दिया गया है - कृपया अपनी देखभाल टीम से नया कोड मांगें।",
                   "wrong_dob": "जन्मतिथि हमारे रिकॉर्ड से मेल नहीं खाती। कृपया जांचें और फिर से कोशिश करें।",
                   "mismatch": "दोनों पासवर्ड एक जैसे नहीं हैं।",
                   "weak": "कृपया मज़बूत पासवर्ड चुनें: कम से कम 12 अक्षर, आम पासवर्ड नहीं, और आपका नाम नहीं।",
                   "rate_limited": "इस डिवाइस से बहुत सारी कोशिशें। कृपया 15 मिनट रुककर फिर से कोशिश करें।",
                   "missing": "कृपया हर जानकारी भरें।", "expired_page": "पेज की समय-सीमा खत्म हो गई। कृपया फिर से कोशिश करें।"}},
    "ur": {"title": "اپنا مریض اکاؤنٹ فعال کریں", "intro": "اپنے ٹیکسٹ پیغام یا نگہداشت ٹیم سے ملا ایکٹیویشن کوڈ اور اپنی تاریخ پیدائش درج کریں، پھر پاس ورڈ منتخب کریں۔",
           "code": "ایکٹیویشن کوڈ", "code_hint": "8 حروف اور ہندسے، جیسے ABCD-2345", "code_from_link": "آپ کے لنک سے کوڈ خود بخود بھر دیا گیا ہے۔",
           "dob": "آپ کی تاریخ پیدائش", "pw": "پاس ورڈ منتخب کریں", "pw_hint": "کم از کم 12 حروف۔ تین یا چار غیر متعلق الفاظ یاد رکھنا آسان ہوتا ہے۔",
           "pw2": "پاس ورڈ دوبارہ لکھیں", "submit": "میرا اکاؤنٹ فعال کریں", "show": "دکھائیں", "hide": "چھپائیں",
           "done_title": "آپ کا اکاؤنٹ تیار ہے", "done_body": "آپ کا یوزر نیم ہے", "done_keep": "اسے محفوظ رکھیں - سائن ان کے لیے پاس ورڈ کے ساتھ اس کی ضرورت ہوگی۔",
           "go": "میرا صفحہ کھولیں", "have_account": "پہلے ہی فعال کر چکے ہیں؟", "sign_in": "سائن ان کریں",
           "err": {"invalid": "یہ ایکٹیویشن کوڈ درست نہیں ہے۔ کوڈ چیک کریں اور دوبارہ کوشش کریں۔",
                   "expired": "اس ایکٹیویشن کوڈ کی میعاد ختم ہو گئی ہے۔ براہ کرم اپنی نگہداشت ٹیم سے نیا کوڈ مانگیں۔",
                   "used": "یہ ایکٹیویشن کوڈ پہلے ہی استعمال ہو چکا ہے۔ اپنے یوزر نیم اور پاس ورڈ سے سائن ان کریں۔",
                   "revoked": "یہ ایکٹیویشن کوڈ اب درست نہیں ہے۔ براہ کرم اپنی نگہداشت ٹیم سے نیا کوڈ مانگیں۔",
                   "locked": "بہت زیادہ غلط کوششیں۔ یہ کوڈ روک دیا گیا ہے - براہ کرم اپنی نگہداشت ٹیم سے نیا کوڈ مانگیں۔",
                   "wrong_dob": "تاریخ پیدائش ہمارے ریکارڈ سے مطابقت نہیں رکھتی۔ براہ کرم چیک کریں اور دوبارہ کوشش کریں۔",
                   "mismatch": "دونوں پاس ورڈ ایک جیسے نہیں ہیں۔",
                   "weak": "براہ کرم مضبوط پاس ورڈ منتخب کریں: کم از کم 12 حروف، عام پاس ورڈ نہیں، اور آپ کا نام نہیں۔",
                   "rate_limited": "اس ڈیوائس سے بہت زیادہ کوششیں۔ براہ کرم 15 منٹ انتظار کر کے دوبارہ کوشش کریں۔",
                   "missing": "براہ کرم ہر خانہ پُر کریں۔", "expired_page": "صفحے کی میعاد ختم ہو گئی۔ براہ کرم دوبارہ کوشش کریں۔"}},
}
ACTIVATE_LANGS = {"en": "English", "ar": "العربية", "hi": "हिन्दी", "ur": "اردو"}


def page_language(db: Session, token: str | None, requested: str | None) -> str:
    if requested in ACTIVATE_TEXT:
        return requested  # type: ignore[return-value]
    if token:
        inv = find_invite(db, token=token)
        p = db.get(Patient, inv.patient_id) if inv else None
        if p is not None and p.language in ACTIVATE_TEXT:
            return p.language
    return "en"


def error_text(lang: str, reason: str) -> str:
    t = ACTIVATE_TEXT[lang]["err"]
    if reason.startswith("weak:"):
        return t["weak"] + ((" " + reason[5:]) if lang == "en" else "")
    return t.get(reason, t["invalid"])
