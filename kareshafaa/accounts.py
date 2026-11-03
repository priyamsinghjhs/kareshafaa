"""Real per-user accounts: argon2id password hashing, password policy, server-side sessions, CSRF tokens,
login rate limiting / temporary lockout, and demo-account provisioning.

Security rules (tested in tests/test_auth_accounts.py):
* passwords are stored only as argon2id hashes; no API ever returns a hash;
* the session cookie holds a random token; the database stores only its SHA-256; sessions expire after
  settings.session_idle_minutes of inactivity and settings.token_ttl_minutes in total, and are revoked on logout,
  password change/reset and deactivation;
* every cookie-authenticated write must carry the session's CSRF token (header X-CSRF-Token or form field);
* failed logins are counted per username (per username + client IP in public demo mode, so nobody can lock the
  shared demo accounts for everyone) and per client IP; error messages never say which part was wrong;
* demo-account passwords come only from the environment, or (outside public demo mode) are generated at random
  and written to a 0600 file outside the git repository. They are never in the repo or shown on the site.
"""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import logging
import os
import re
import secrets
import string
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .config import BASE_DIR, Settings, get_settings
from .models import LoginAttempt, User, UserSession, utcnow

log = logging.getLogger("kareshafaa.accounts")

# argon2id, OWASP minimum profile (19 MiB, t=2, p=1): safe on a 512 MB free instance
_PH = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, hash_len=32, salt_len=16)
_DUMMY_HASH = _PH.hash("dummy-password-for-timing-equalisation")

ROLE_LABELS = {
    "coordinator": "Care coordinator",
    "gp": "Family doctor (GP)",
    "specialist": "Specialist doctor",
    "clinical_lead": "Clinical lead",
    "analyst": "Programme analyst",
    "admin": "System administrator",
    "translator": "Medical translator",
    "patient": "Patient",
}
CLINICAL_ROLES = frozenset({"coordinator", "gp", "specialist", "clinical_lead"})
ASSIGNABLE_ROLES = ("coordinator", "gp", "specialist", "clinical_lead", "analyst", "admin", "translator")

# One demo account per role (the existing demo staff), plus the people the demo stories ask you to log in as.
DEMO_ACCOUNTS: tuple[str, ...] = (
    "aisha.rahman",        # coordinator
    "omar.haddad",         # gp
    "vikram.mehta",        # specialist
    "fatima.al.mansoori",  # clinical_lead
    "analyst", "admin", "translator",
    "patient.en",          # patient
    # story extras: receiving GP (two-key transfer), programme director (L4), patients in other languages
    "yousef.al.marzouqi", "layla.al.hashimi", "patient.ar", "patient.hi", "patient.ur", "patient.tl",
)

# --------------------------------------------------------------------------------------------- password policy
COMMON_PASSWORDS = frozenset("""
password passw0rd p@ssword p@ssw0rd qwerty qwertyuiop asdfghjkl zxcvbnm 123456 1234567 12345678 123456789
1234567890 0123456789 111111 000000 abc123 iloveyou admin administrator welcome letmein monkey dragon master
sunshine princess football baseball superman batman trustno1 shadow michael jennifer hunter freedom whatever
secret changeme default login access starwars computer internet samsung google mustang charlie donald
loveme hello welcome1 qazwsx 1q2w3e4r 1qaz2wsx zaq12wsx password1 password123 qwerty123 admin123 root toor
test testing guest user demo demo123 kareshafaa healthcare hospital doctor nurse patient clinic medical
dubai abudhabi emirates uae ramadan summer winter spring autumn january february december
correcthorsebatterystaple letmeinplease passwordpassword qwertyqwerty abcdefghijkl abcdefabcdef
""".split())


def password_problems(pw: str, username: str = "", display_name: str = "") -> list[str]:
    """Plain-language reasons a password is not acceptable (empty list = OK)."""
    s = get_settings()
    out: list[str] = []
    if len(pw) < s.password_min_length:
        out.append(f"Use at least {s.password_min_length} characters.")
    if len(pw) > 256:
        out.append("Use at most 256 characters.")
    low = pw.lower()
    core = re.sub(r"[^a-z]", "", low)
    stripped = re.sub(r"[\d\W_]+$", "", low)  # Password2026! -> password
    if low in COMMON_PASSWORDS or core in COMMON_PASSWORDS or stripped in COMMON_PASSWORDS:
        out.append("This password is too common. Choose something less predictable, such as three or four "
                   "unrelated words.")
    elif len(set(pw)) <= 3 or re.fullmatch(r"(.{1,4})\1+", pw):
        out.append("Avoid repeated characters or patterns.")
    elif low in "abcdefghijklmnopqrstuvwxyz0123456789" or low in "qwertyuiopasdfghjklzxcvbnm":
        out.append("Avoid keyboard or alphabet sequences.")
    for part in (username.lower(), *display_name.lower().split()):
        if len(part) >= 4 and part in low:
            out.append("Do not include your name or username.")
            break
    return out


def hash_password(pw: str) -> str:
    return _PH.hash(pw)


def verify_password(stored: str | None, pw: str) -> bool:
    if not stored:
        try:
            _PH.verify(_DUMMY_HASH, pw)  # equalise timing for accounts without a password
        except VerificationError:
            pass
        return False
    try:
        return _PH.verify(stored, pw)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def generate_password(n: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits + "-_.!@#%"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(n))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw)
                and not password_problems(pw)):
            return pw


def set_password(user: User, pw: str, *, must_change: bool = False) -> None:
    user.password_hash = hash_password(pw)
    user.must_change_password = must_change
    user.password_changed_at = utcnow()
    user.locked_until = None


# --------------------------------------------------------------------------------------------- sessions / CSRF
SESSION_PREFIX = "s1."


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_for(token: str) -> str:
    """CSRF token bound to a session token (HMAC with the app secret); nothing extra to store."""
    key = get_settings().secret_key.encode()  # type: ignore[union-attr]
    return hmac.new(key, b"csrf|" + token.encode(), hashlib.sha256).hexdigest()[:40]


def csrf_ok(token: str | None, presented: str | None) -> bool:
    return bool(token and presented) and hmac.compare_digest(csrf_for(token), presented)  # type: ignore[arg-type]


def create_session(db: Session, user: User, ip: str | None = None, user_agent: str | None = None) -> str:
    s = get_settings()
    token = SESSION_PREFIX + secrets.token_urlsafe(32)
    now = utcnow()
    db.add(UserSession(token_hash=_token_hash(token), user_id=user.id, created_at=now, last_seen_at=now,
                       expires_at=now + timedelta(minutes=s.token_ttl_minutes), ip=(ip or "")[:64] or None,
                       user_agent=(user_agent or "")[:200] or None))
    db.flush()
    return token


def lookup_session(db: Session, token: str) -> tuple[UserSession, User] | None:
    """Valid (not revoked, not expired, not idle) session and its ACTIVE user, else None. Read only."""
    if not token.startswith(SESSION_PREFIX):
        return None
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == _token_hash(token)))
    if sess is None or sess.revoked_at is not None:
        return None
    now = utcnow()
    idle = timedelta(minutes=get_settings().session_idle_minutes)
    if sess.expires_at <= now or sess.last_seen_at + idle <= now:
        return None
    u = db.get(User, sess.user_id)
    if u is None or not u.active:
        return None
    return sess, u


def touch_session(session_factory, token: str) -> None:
    """Sliding idle timeout: bump last_seen_at at most once a minute (own short transaction, best effort)."""
    try:
        db = session_factory()()
        try:
            cutoff = utcnow() - timedelta(seconds=60)
            db.execute(update(UserSession).where(UserSession.token_hash == _token_hash(token),
                                                 UserSession.last_seen_at < cutoff).values(last_seen_at=utcnow()))
            db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - never fail a request because the idle clock could not be bumped
        log.debug("session touch failed", exc_info=True)


def revoke_session(db: Session, token: str) -> None:
    db.execute(update(UserSession).where(UserSession.token_hash == _token_hash(token),
                                         UserSession.revoked_at.is_(None)).values(revoked_at=utcnow()))


def revoke_user_sessions(db: Session, user_id: int, except_token: str | None = None) -> None:
    q = update(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
    if except_token:
        q = q.where(UserSession.token_hash != _token_hash(except_token))
    db.execute(q.values(revoked_at=utcnow()))


# --------------------------------------------------------------------------------------------- login
GENERIC_LOGIN_ERROR = "That username and password did not match. Please try again."
LOCKED_ERROR = "Too many failed attempts. For your security, sign-in is paused for a few minutes. Please try later."


@dataclass
class LoginResult:
    user: User | None
    reason: str  # ok | bad_credentials | unknown_user | inactive | no_password | locked | rate_limited

    @property
    def ok(self) -> bool:
        return self.user is not None and self.reason == "ok"


def _failures_since(db: Session, username: str | None, ip: str | None, since) -> int:
    q = select(func.count()).select_from(LoginAttempt).where(LoginAttempt.success.is_(False), LoginAttempt.at > since)
    if username is not None:
        q = q.where(LoginAttempt.username == username)
    if ip is not None:
        q = q.where(LoginAttempt.ip == ip)
    return int(db.scalar(q) or 0)


def _last_success(db: Session, username: str, ip: str | None):
    q = select(func.max(LoginAttempt.at)).where(LoginAttempt.username == username, LoginAttempt.success.is_(True))
    if ip is not None:
        q = q.where(LoginAttempt.ip == ip)
    return db.scalar(q)


def is_locked(db: Session, username: str, ip: str | None) -> bool:
    s = get_settings()
    now = utcnow()
    scope_ip = ip if s.public_demo else None  # public demo: per account + IP (shared accounts cannot be DoS-ed)
    since = now - timedelta(minutes=s.login_window_minutes)
    last_ok = _last_success(db, username, scope_ip)
    if last_ok and last_ok > since:
        since = last_ok
    u = db.scalar(select(User).where(User.username == username)) if not s.public_demo else None
    if u is not None and u.password_changed_at and u.password_changed_at > since:
        since = u.password_changed_at  # a reset (admin, or a patient reset code) clears earlier failures
    if _failures_since(db, username, scope_ip, since) >= s.login_max_failures:
        return True
    if u is not None and u.locked_until and u.locked_until > now:
        return True
    return False


def authenticate(db: Session, username: str, password: str, ip: str | None) -> LoginResult:
    s = get_settings()
    uname = (username or "").strip().lower()[:80]
    now = utcnow()
    window = now - timedelta(minutes=s.login_window_minutes)
    if ip and _failures_since(db, None, ip, window) >= s.login_ip_max_failures:
        verify_password(None, password)
        return LoginResult(None, "rate_limited")
    if is_locked(db, uname, ip):
        verify_password(None, password)
        db.add(LoginAttempt(username=uname, ip=ip, at=now, success=False))
        return LoginResult(None, "locked")
    u = db.scalar(select(User).where(User.username == uname))
    ok = verify_password(u.password_hash if u is not None else None, password)
    reason = "ok"
    if u is None:
        reason = "unknown_user"
    elif not u.password_hash:
        reason = "no_password"
    elif not ok:
        reason = "bad_credentials"
    elif not u.active:
        reason = "inactive"
    db.add(LoginAttempt(username=uname, ip=ip, at=now, success=reason == "ok"))
    if reason != "ok":
        db.flush()
        if u is not None and not s.public_demo and is_locked(db, uname, ip):
            u.locked_until = now + timedelta(minutes=s.login_lockout_minutes)
        return LoginResult(None, reason)
    u.last_login_at = now
    u.locked_until = None
    try:
        if _PH.check_needs_rehash(u.password_hash):  # type: ignore[arg-type]
            u.password_hash = hash_password(password)
    except InvalidHashError:
        pass
    return LoginResult(u, "ok")


def public_user(u: User) -> dict:
    """The ONLY serialisation of a user for APIs: never includes the password hash."""
    return {"id": u.id, "username": u.username, "display_name": u.display_name, "role": u.role,
            "role_label": ROLE_LABELS.get(u.role, u.role), "site": u.site, "team": u.team, "active": bool(u.active),
            "must_change_password": bool(u.must_change_password), "has_password": bool(u.password_hash),
            "locked": bool(u.locked_until and u.locked_until > utcnow()),
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "is_demo_account": bool(u.is_demo_account), "practitioner_id": u.practitioner_id,
            "status_reason": u.status_reason, "status_changed_by": u.status_changed_by,
            "status_changed_at": u.status_changed_at.isoformat() if u.status_changed_at else None}


# --------------------------------------------------------------------------------------------- demo accounts
def _env_password_for(role: str, s: Settings) -> str | None:
    return os.environ.get(f"KARESHAFAA_DEMO_PASSWORD_{role.upper()}") or s.demo_password or None


def credentials_path(s: Settings) -> Path:
    if s.demo_credentials_file:
        return Path(s.demo_credentials_file)
    if s.env == "test":
        return s.data_dir / "demo_credentials.txt"
    return BASE_DIR.parent / "demo_credentials.txt"


def _write_credentials(s: Settings, rows: list[tuple[str, str, str]]) -> Path | None:
    body = ("# KareShafaa LOCAL demo accounts (synthetic data). Generated at random; keep private; never commit.\n"
            "# username\trole\tpassword\n" + "".join(f"{u}\t{r}\t{p}\n" for u, r, p in rows))
    for path in (credentials_path(s), s.data_dir / "demo_credentials.txt"):
        try:
            repo = BASE_DIR.resolve()
            if repo in path.resolve().parents and path.resolve().parent != (s.data_dir.resolve()):
                continue  # never inside the git working tree (data/ is git-ignored)
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(body)
            os.chmod(path, 0o600)
            return path
        except OSError:
            continue
    return None


def provision_demo_accounts(db: Session, s: Settings | None = None) -> dict:
    """Give the demo accounts usable passwords (idempotent). Returns a summary without any password."""
    s = s or get_settings()
    users = {u.username: u for u in db.scalars(select(User).where(User.username.in_(DEMO_ACCOUNTS)))}
    if not users:
        return {"provisioned": 0, "mode": "no demo users in this database"}
    for u in users.values():
        u.is_demo_account = True
        if not u.site and u.practitioner is not None and u.practitioner.org is not None:
            u.site = u.practitioner.org.name
        if not u.team:
            u.team = ROLE_LABELS.get(u.role)
    env_any = any(_env_password_for(u.role, s) for u in users.values())
    if s.mode == "production":
        # Working system: the environment password only gives a FIRST password to staff accounts that have none.
        # Passwords people have changed, or an admin has reset, are never overwritten on restart.
        missing, set_now = [], 0
        for u in users.values():
            if u.password_hash:
                continue
            pw = _env_password_for(u.role, s)
            if not pw or password_problems(pw):
                missing.append(u.username)
                continue
            set_password(u, pw, must_change=False)
            set_now += 1
        db.commit()
        if missing:
            log.warning("STAFF ACCOUNTS WITHOUT A PASSWORD: %d account(s) have no password yet and "
                        "KARESHAFAA_DEMO_PASSWORD is %s. They cannot sign in until it is set to a strong value "
                        "(12+ characters, not a common password) and the service restarts, or an administrator "
                        "resets their password.", len(missing), "unset" if not env_any else "blank or too weak")
        return {"provisioned": set_now, "changed": set_now, "unusable": len(missing), "mode": "production"}
    if env_any or s.public_demo:
        weak, changed = [], 0
        for u in users.values():
            pw = _env_password_for(u.role, s)
            if not pw or password_problems(pw):
                weak.append(u.username)
                if u.password_hash:
                    u.password_hash = None  # refuse a blank/weak demo password: account not usable
                continue
            if not verify_password(u.password_hash, pw) or u.must_change_password:
                set_password(u, pw, must_change=False)
                changed += 1
        db.commit()
        if weak:
            log.warning("DEMO ACCOUNTS NOT USABLE: KARESHAFAA_DEMO_PASSWORD (or KARESHAFAA_DEMO_PASSWORD_<ROLE>) is %s "
                        "for %d account(s). The site starts and shows the login page, but nobody can sign in to "
                        "these demo accounts. Set a strong value (12+ characters, not a common password) in the "
                        "environment / Render dashboard and restart.",
                        "unset" if not env_any else "blank or too weak", len(weak))
        return {"provisioned": len(users) - len(weak), "changed": changed, "unusable": len(weak), "mode": "env"}
    # local / dev without an env password: random passwords, written once to a private file
    path = credentials_path(s)
    if all(u.password_hash for u in users.values()) and (path.exists() or (s.data_dir / "demo_credentials.txt").exists()):
        db.commit()
        return {"provisioned": len(users), "changed": 0, "mode": "random (existing)", "file": str(path)}
    rows = []
    for name in DEMO_ACCOUNTS:
        u = users.get(name)
        if u is None:
            continue
        pw = generate_password()
        set_password(u, pw, must_change=False)
        rows.append((u.username, ROLE_LABELS.get(u.role, u.role), pw))
    written = _write_credentials(s, rows)
    db.commit()
    if written is None:
        log.warning("demo accounts got random passwords but no credentials file could be written; set "
                    "KARESHAFAA_DEMO_PASSWORD or KARESHAFAA_DEMO_CREDENTIALS_FILE and restart")
    else:
        log.warning("demo accounts: random passwords written to %s (mode 0600, outside the git repo)", written)
    return {"provisioned": len(rows), "changed": len(rows), "mode": "random", "file": str(written) if written else None}


def provision_on_startup() -> None:
    """Called from the app lifespan (every worker) and after a public-demo reset; serialised with a file lock."""
    from .db import session_factory

    s = get_settings()
    if not s.provision_demo_accounts:
        return
    fd = os.open(str(s.data_dir / ".demo-accounts.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        db = session_factory()()
        try:
            out = provision_demo_accounts(db, s)
            log.info("demo accounts: %s", out)
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - the app must still start and show the login page
        log.exception("demo account provisioning failed")
    finally:
        os.close(fd)
