"""Test inboxes for one account of each role, so outbound mail can be checked without
emailing the ~2000 synthetic sample patients.

Staff addresses are stored on User.email. The patient.en address is stored on that
user's Patient.email (patient messages use the patient record, not User.email).
"""
from __future__ import annotations

STAFF_MAILBOXES: dict[str, str] = {
    "admin": "testingkareshafaa+calpha_admin@gmail.com",
    "fatima.al.mansoori": "testingkareshafaa+clead1234@gmail.com",
    "aisha.rahman": "testingkareshafaa+ccalpha1234@gmail.com",
    "omar.haddad": "testingkareshafaa+rehmanmalik1234@gmail.com",
    "vikram.mehta": "testingkareshafaa+splist123@gmail.com",
    "analyst": "testingkareshafaa+program_analysts@gmail.com",
    "translator": "testingkareshafaa+medi_trans@gmail.com",
}

PATIENT_MAILBOXES: dict[str, str] = {
    "patient.en": "testingkareshafaa+asimkhan123@gmail.com",
}

# Validated (lower-case) addresses that a SYN- patient may receive mail at, without
# turning on KARESHAFAA_MAIL_ALLOW_SYNTHETIC for the whole sample caseload.
ALLOWLISTED_PATIENT_ADDRESSES: frozenset[str] = frozenset(a.lower() for a in PATIENT_MAILBOXES.values())


def staff_mailbox(username: str) -> str | None:
    return STAFF_MAILBOXES.get((username or "").strip().lower())


def patient_mailbox(username: str) -> str | None:
    return PATIENT_MAILBOXES.get((username or "").strip().lower())


def is_allowlisted_patient_address(addr: str | None) -> bool:
    return (addr or "").strip().lower() in ALLOWLISTED_PATIENT_ADDRESSES
