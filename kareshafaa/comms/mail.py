"""Outbound e-mail from the app's own mailbox (default kareshafaa_com@outlook.com).

OFF until a sign-in secret is set on the host (e.g. Render -> Environment). Without it nothing is sent and every
patient message stays simulated, exactly as before. Standard library only: no paid e-mail provider.

Sign-in and transport:
- OAuth2 (needed for a personal Outlook.com mailbox, which refuses password sign-in since Sept 2024):
  KARESHAFAA_MAIL_OAUTH_CLIENT_ID + KARESHAFAA_MAIL_OAUTH_REFRESH_TOKEN, obtained once with
  ``python scripts/outlook_oauth_setup.py`` (the mailbox owner signs in at Microsoft). Default transport is
  Microsoft Graph ``/me/sendMail`` over HTTPS (port 443), because Render's free plan blocks outbound SMTP ports
  25/465/587. KARESHAFAA_MAIL_TRANSPORT=smtp uses SMTP XOAUTH2 instead (paid host / elsewhere).
- Password: KARESHAFAA_SMTP_PASSWORD over SMTP + STARTTLS on 587 (providers that still allow password SMTP AUTH).

Safety rules:
- Secrets (password, refresh/access tokens) are never logged, audited, returned by an API or put in an exception
  message; errors are reduced to a short reason without server reply text (which can echo the account name).
- Synthetic sample records (MRN ``SYN-...``) and reserved example domains (example.com/.org/.net) are never e-mailed
  (SYN- only with KARESHAFAA_MAIL_ALLOW_SYNTHETIC, or when the address is an allowlisted test inbox in
  ``synth/test_mailboxes.py``), so the ~2000 sample patients on the live site cannot flood the mailbox or bounce.
- E-mail is not end-to-end encrypted (TLS protects it only in transit to the mail service).

Callers use ``send_email`` only (comms/service.py, services/escalation.py, the admin test send); the transports are
``_graph_send`` and ``_smtp_send``.
"""
from __future__ import annotations

import json
import logging
import smtplib
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from ..config import get_settings

log = logging.getLogger("kareshafaa.mail")

CONSUMER_MS_DOMAINS = ("outlook.com", "hotmail.com", "live.com", "msn.com")
SYNTHETIC_DOMAINS = ("example.com", "example.org", "example.net")
FROM_NAME = "KareShafaa"
GRAPH_SEND_URL = "https://graph.microsoft.com/v1.0/me/sendMail"
SCOPES = {"graph": "https://graph.microsoft.com/Mail.Send offline_access",
          "smtp": "https://outlook.office.com/SMTP.Send offline_access"}


class MailError(RuntimeError):
    """Sending failed. The message is short and never contains a secret or connection details."""


class MailNotConfigured(MailError):
    pass


class _OAuthError(Exception):
    pass


class _GraphError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(f"{status} {code}")
        self.status, self.code = status, code


def from_address() -> str:
    return (get_settings().mail_from or "").strip()


def smtp_host() -> str:
    s = get_settings()
    if (s.smtp_host or "").strip():
        return s.smtp_host.strip()
    domain = from_address().rsplit("@", 1)[-1].lower()
    return "smtp-mail.outlook.com" if domain in CONSUMER_MS_DOMAINS else "smtp.office365.com"


def auth_method() -> str | None:
    s = get_settings()
    if (s.mail_oauth_client_id or "").strip() and (s.mail_oauth_refresh_token or "").strip():
        return "oauth2"
    if (s.smtp_password or "").strip():
        return "password"
    return None


def transport() -> str | None:
    """'graph' (HTTPS, works on Render's free plan) or 'smtp'. Graph needs OAuth; a password means SMTP."""
    m = auth_method()
    if m is None:
        return None
    t = (get_settings().mail_transport or "auto").strip().lower()
    if m == "password":
        return "smtp"
    return "smtp" if t == "smtp" else "graph"


def configured() -> bool:
    """True only when a sign-in secret is set (and there is a From address)."""
    return auth_method() is not None and "@" in from_address()


def status() -> dict:
    """For /healthz and Admin > System: whether e-mail is on, from which address and how (never a secret)."""
    s = get_settings()
    t = transport()
    return {"configured": configured(), "from": from_address() or None, "auth": auth_method(), "transport": t,
            "host": "graph.microsoft.com" if t == "graph" else smtp_host(), "port": 443 if t == "graph" else int(s.smtp_port),
            "alerts_to_set": bool((s.alert_to or "").strip()), "allow_synthetic": bool(s.mail_allow_synthetic)}


def validate_address(addr: str | None) -> str:
    from ..services.errors import Invalid  # noqa: PLC0415
    from ..services.patients import validate_email  # noqa: PLC0415 - avoid an import cycle

    e = validate_email(addr)
    if not e:
        raise Invalid("an e-mail address is required")
    return e


def is_synthetic_address(addr: str | None) -> bool:
    d = (addr or "").rsplit("@", 1)[-1].strip().lower()
    return any(d == x or d.endswith("." + x) for x in SYNTHETIC_DOMAINS)


def is_synthetic_patient(patient) -> bool:
    return (getattr(patient, "mrn", "") or "").upper().startswith("SYN-") or is_synthetic_address(getattr(patient, "email", None))


def can_send_to_address(addr: str | None) -> bool:
    """Configured, a valid address, and not a reserved example domain (example.com / .org / .net)."""
    if not configured() or not (addr or "").strip():
        return False
    try:
        addr = validate_address(addr)
    except Exception:  # noqa: BLE001 - an invalid stored address is simply not e-mailed
        return False
    if is_synthetic_address(addr):
        log.info("e-mail skipped: reserved example domain placeholder (RFC 2606)")
        return False
    return True


def can_send_to_patient(patient) -> bool:
    """Configured, the patient has a valid e-mail, the domain is not a reserved example domain, and the record is not a
    SYN- sample (unless KARESHAFAA_MAIL_ALLOW_SYNTHETIC, or the address is an allowlisted test inbox)."""
    if patient is None or not can_send_to_address(getattr(patient, "email", None)):
        return False
    if (patient.mrn or "").upper().startswith("SYN-"):
        from ..synth.test_mailboxes import is_allowlisted_patient_address  # noqa: PLC0415
        if is_allowlisted_patient_address(patient.email):
            return True
        if not get_settings().mail_allow_synthetic:
            return False
    return True


def alerts_enabled() -> bool:
    return configured() and bool((get_settings().alert_to or "").strip())


# ---------------------------------------------------------------- OAuth2 token (refresh token -> access token)
_TOKEN: dict = {}  # in memory only: access token, expiry, rotated refresh token (never logged or stored)
_TOKEN_LOCK = threading.Lock()


def _token_url() -> str:
    tenant = (get_settings().mail_oauth_tenant or "consumers").strip()
    return f"https://login.microsoftonline.com/{urllib.parse.quote(tenant, safe='')}/oauth2/v2.0/token"


def _http_post_form(url: str, data: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https Microsoft endpoint
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            code = json.loads(e.read().decode()).get("error", "")
        except Exception:  # noqa: BLE001
            code = ""
        raise _OAuthError(code or f"http {e.code}") from None


def _access_token(kind: str) -> str:
    """A fresh access token for 'graph' or 'smtp' (cached until shortly before it expires)."""
    s = get_settings()
    key = (s.mail_oauth_refresh_token, kind)
    with _TOKEN_LOCK:
        if _TOKEN.get("for") == key and _TOKEN.get("access") and _TOKEN.get("exp", 0) > time.time() + 120:
            return _TOKEN["access"]
        rt = _TOKEN.get("rotated") if _TOKEN.get("rt_for") == s.mail_oauth_refresh_token else None
        out = _http_post_form(_token_url(), {"client_id": s.mail_oauth_client_id.strip(), "grant_type": "refresh_token",
                                             "refresh_token": rt or s.mail_oauth_refresh_token.strip(),
                                             "scope": SCOPES[kind]}, float(s.smtp_timeout_s))
        if not out.get("access_token"):
            raise _OAuthError("no access token")
        _TOKEN.update({"access": out["access_token"], "exp": time.time() + int(out.get("expires_in", 3600)),
                       "rotated": out.get("refresh_token") or rt, "rt_for": s.mail_oauth_refresh_token, "for": key})
        return _TOKEN["access"]


# ---------------------------------------------------------------- transports
def _http_post_json(url: str, payload: dict, token: str, timeout: float) -> int:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https Microsoft Graph endpoint
            return r.status
    except urllib.error.HTTPError as e:
        try:
            code = (json.loads(e.read().decode()).get("error") or {}).get("code", "")
        except Exception:  # noqa: BLE001
            code = ""
        raise _GraphError(e.code, code) from None


def _graph_send(msg: EmailMessage) -> None:
    """Microsoft Graph sendMail (HTTPS 443) as the signed-in mailbox; saved to its Sent Items."""
    s = get_settings()
    m = {"subject": str(msg["Subject"] or ""), "body": {"contentType": "Text", "content": msg.get_content()},
         "toRecipients": [{"emailAddress": {"address": str(msg["To"])}}]}
    if msg["Reply-To"]:
        m["replyTo"] = [{"emailAddress": {"address": str(msg["Reply-To"])}}]
    _http_post_json(GRAPH_SEND_URL, {"message": m, "saveToSentItems": True}, _access_token("graph"),
                    float(s.smtp_timeout_s))


def _smtp_send(msg: EmailMessage) -> None:
    """SMTP submission with STARTTLS (never plain text, never port 25); XOAUTH2 or password sign-in."""
    s = get_settings()
    port = int(s.smtp_port)
    if port == 25:
        raise MailError("port 25 is not used: set KARESHAFAA_SMTP_PORT=587 (STARTTLS)")
    ctx = ssl.create_default_context()
    with smtplib.SMTP(smtp_host(), port, timeout=float(s.smtp_timeout_s)) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ctx)
        smtp.ehlo()
        user = (s.smtp_username or "").strip() or from_address()
        if auth_method() == "oauth2":
            token = _access_token("smtp")
            smtp.auth("XOAUTH2", lambda challenge=None: f"user={user}\x01auth=Bearer {token}\x01\x01")
        else:
            smtp.login(user, s.smtp_password)
        smtp.send_message(msg)


def _safe_reason(e: BaseException) -> str:
    """A short reason with no secrets or server reply text."""
    rerun = "run scripts/outlook_oauth_setup.py again and update KARESHAFAA_MAIL_OAUTH_REFRESH_TOKEN"
    if isinstance(e, _OAuthError):
        code = str(e)
        if code == "invalid_grant":
            return f"Microsoft refused the saved sign-in (expired, revoked or missing permission): {rerun}"
        return f"Microsoft sign-in failed ({code[:40] if code.replace('_', '').isalnum() else 'error'})"
    if isinstance(e, _GraphError):
        if e.status == 401:
            return f"Microsoft Graph refused the sign-in: {rerun}"
        if e.status == 403:
            return "Microsoft Graph refused to send (the app registration needs the delegated Mail.Send permission)"
        if e.status == 429:
            return "Microsoft Graph is throttling sending; try again later"
        if e.status == 400:
            return "Microsoft Graph refused the message (check the recipient address)"
        return f"Microsoft Graph returned error {e.status}"
    if isinstance(e, smtplib.SMTPAuthenticationError) and b"5.7.139" in (e.smtp_error or b""):
        if auth_method() == "password":
            return ("the mail server refused password sign-in (Outlook.com needs OAuth: set "
                    "KARESHAFAA_MAIL_OAUTH_CLIENT_ID and KARESHAFAA_MAIL_OAUTH_REFRESH_TOKEN)")
        return "the mail server refused the sign-in: SMTP sending is disabled for this mailbox (error 5.7.139)"
    if isinstance(e, smtplib.SMTPAuthenticationError):
        return "the mail server refused the sign-in (check the mailbox sign-in settings)"
    if isinstance(e, smtplib.SMTPRecipientsRefused):
        return "the mail server refused the recipient address"
    if isinstance(e, smtplib.SMTPSenderRefused):
        return "the mail server refused the From address"
    if isinstance(e, smtplib.SMTPNotSupportedError):
        return "the mail server does not support STARTTLS"
    if isinstance(e, (socket.timeout, TimeoutError)):
        return "the mail service did not answer in time (SMTP ports are blocked on Render's free plan)"
    if isinstance(e, smtplib.SMTPResponseException):
        return f"the mail server returned error {e.smtp_code}"
    if isinstance(e, (urllib.error.URLError, OSError)) and not isinstance(e, smtplib.SMTPException):
        return "could not connect to the mail service"
    return f"sending failed ({type(e).__name__})"


def build_message(to: str, subject: str, body: str, *, reply_to: str | None = None) -> EmailMessage:
    msg = EmailMessage()
    sender = from_address()
    msg["From"] = formataddr((FROM_NAME, sender))
    msg["To"] = to
    msg["Subject"] = subject.replace("\r", " ").replace("\n", " ")[:200]
    msg["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1] or None)
    if reply_to:
        msg["Reply-To"] = validate_address(reply_to)
    msg.set_content(body)
    return msg


def send_email(to: str, subject: str, body: str, *, reply_to: str | None = None) -> None:
    """Send one plain-text e-mail from the configured mailbox. Raises MailNotConfigured / MailError (secret-free
    messages) on failure."""
    if not configured():
        raise MailNotConfigured("e-mail is not configured (set KARESHAFAA_MAIL_OAUTH_CLIENT_ID + "
                                "KARESHAFAA_MAIL_OAUTH_REFRESH_TOKEN, or KARESHAFAA_SMTP_PASSWORD, on the server)")
    addr = validate_address(to)
    msg = build_message(addr, subject, body, reply_to=reply_to)
    try:
        (_graph_send if transport() == "graph" else _smtp_send)(msg)
    except MailError:
        raise
    except Exception as e:  # noqa: BLE001 - reduce every transport error to a safe reason
        reason = _safe_reason(e)
        log.warning("e-mail not sent: %s", reason)  # no address, no secrets
        raise MailError(reason) from None
    log.info("e-mail sent (%s)", subject[:60])
