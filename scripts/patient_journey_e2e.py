"""End-to-end check of the PATIENT side of the screening-to-care loop, in a real browser.

  KARESHAFAA_STAFF_CREDENTIALS_FILE=... python scripts/patient_journey_e2e.py http://127.0.0.1:8021 /tmp/pj/k.db OUT_DIR
  (or KARESHAFAA_STAFF_PASSWORD=...; the password is never printed or passed on the command line)

Run it against a LOCAL production-mode server on a COPY of a SQLite database - it creates a patient, results, an
app account and appointments. It refuses to run against the live site or a non-local address, and refuses the
repository's own data/ databases.

Journey: staff register a patient -> create the app account (one-time code) -> activation: wrong date of birth,
revoked code, expired code, lockout after 5 wrong dates, success in Arabic, re-used code -> results (normal and
follow-up) -> the patient's step tracker at every stage -> appointment booked, confirmed, moved, reminder, change
request (never moves it), cancelled, rebooked, attended -> care plan -> closed -> next screening date -> messages
(simulated) -> contact details -> contact consent -> sign out. Pixel 7 + iPhone 14 emulation (Chrome engine) and a
laptop viewport; English and Arabic (RTL) throughout, Hindi and Urdu spot checks; role separation (the patient
cannot open staff screens or other patients' data). Screenshots: OUT_DIR/patient_<screen>_<view>.png.
"""
from __future__ import annotations

import ipaddress
import json
import os
import random
import re
import sqlite3
import string
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kareshafaa.services.patients import emirates_id_check_digit  # noqa: E402  (the app's own algorithm)

LIVE_HOSTS = ("onrender.com", "kareshafaa.onrender.com")


def refuse_unsafe(base: str, db: str) -> None:
    """Never the live site, never a remote host, never the repository's own databases."""
    host = (urlparse(base).hostname or "").lower()
    if not host or any(host == h or host.endswith("." + h) for h in LIVE_HOSTS) or "onrender" in base.lower():
        raise SystemExit(f"refusing to run against {base!r}: this script creates patients - use a local server")
    local = host in ("localhost",)
    try:
        local = local or ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    if not local:
        raise SystemExit(f"refusing to run against non-local host {host!r}")
    p = Path(db).resolve()
    if not p.is_file() or p.suffix != ".db":
        raise SystemExit(f"database {db!r} must be an existing local SQLite file (a copy)")
    if (ROOT / "data").resolve() in p.parents:
        raise SystemExit("refusing the repository's own data/ database: copy it first (sqlite backup) and point the server at the copy")
    if os.environ.get("DATABASE_URL") or "postgres" in os.environ.get("KARESHAFAA_DATABASE_URL", ""):
        raise SystemExit("refusing: a PostgreSQL connection is configured in this environment")


def staff_password() -> str:
    cf = os.environ.get("KARESHAFAA_STAFF_CREDENTIALS_FILE")
    if cf:
        for line in Path(cf).read_text().splitlines():
            parts = line.split("\t")
            if line and not line.startswith("#") and len(parts) == 3:
                return parts[2]
    pw = os.environ.get("KARESHAFAA_STAFF_PASSWORD")
    if not pw:
        raise SystemExit("set KARESHAFAA_STAFF_CREDENTIALS_FILE or KARESHAFAA_STAFF_PASSWORD (never on the command line)")
    return pw


RES: list[dict] = []
COORD, GP, SPEC = "aisha.rahman", "omar.haddad", "vikram.mehta"
PATIENT_PW = "Lantern-Orbit-Cobalt-" + "".join(random.choice(string.digits) for _ in range(3))


def ok(name: str, cond, **info) -> bool:
    RES.append({"check": name, "ok": bool(cond), **({} if cond else info)})
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " " + json.dumps(info, ensure_ascii=False, default=str)[:500]), flush=True)
    return bool(cond)


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    base, db, out = sys.argv[1].rstrip("/"), sys.argv[2], Path(sys.argv[3])
    refuse_unsafe(base, db)
    staff_pw = staff_password()
    out.mkdir(parents=True, exist_ok=True)

    def q(sql, *a, write=False):
        c = sqlite3.connect(db)
        try:
            r = c.execute(sql, a).fetchall()
            if write:
                c.commit()
            return r
        finally:
            c.close()

    def now():
        return datetime.now(timezone.utc)

    with sync_playwright() as pw:
        br = pw.chromium.launch(channel="chrome")
        errors: list[str] = []

        def ctx_for(view: str, locale="en-GB"):
            if view == "laptop":
                opts = {"viewport": {"width": 1366, "height": 900}}
            else:
                d = dict(pw.devices["Pixel 7" if view == "pixel" else "iPhone 14"]); d.pop("default_browser_type", None)
                opts = d
            ctx = br.new_context(**opts, locale=locale, timezone_id="Asia/Dubai")
            return ctx

        def watch(p, who):
            p.on("pageerror", lambda e: errors.append(f"{who}: JS {e}"))
            p.on("console", lambda m: m.type == "error" and "status of 4" not in m.text and errors.append(f"{who}: {m.text}"))

        def login(ctx, user, password, who):
            p = ctx.new_page(); watch(p, who)
            p.goto(base + "/login"); p.fill('input[name="username"]', user); p.fill('input[name="password"]', password)
            p.click('button[type="submit"]'); p.wait_for_load_state("networkidle")
            return p

        def api(p, method, url, body=None):
            tok = p.evaluate("() => (document.querySelector('meta[name=csrf-token]') || {}).content || ''")
            r = p.request.fetch(base + url, method=method, data=json.dumps(body) if body is not None else None,
                                headers={"X-CSRF-Token": tok, "Content-Type": "application/json"})
            try:
                j = r.json()
            except Exception:
                j = r.text()
            return r.status, j

        # ---------------------------------------------------------------- staff
        r0 = br.new_context().request.post(base + "/api/auth/demo-login", data={"username": COORD})
        ok("Server runs in production mode (no one-click sign-in)", r0.status in (403, 404), status=r0.status)
        co = login(ctx_for("laptop"), COORD, staff_pw, "coordinator")
        gp = login(ctx_for("laptop"), GP, staff_pw, "gp")
        sp = login(ctx_for("laptop"), SPEC, staff_pw, "specialist")
        ok("Staff signed in", all("/login" not in x.url for x in (co, gp, sp)), urls=[x.url for x in (co, gp, sp)])

        body_ = "784" + "1972" + f"{random.randint(0, 9_999_999):07d}"
        nid = body_ + str(emirates_id_check_digit(body_))
        fam = "Haddad " + "".join(random.choice("abcdefghijklmnop") for _ in range(6)).capitalize()
        dob = "1972-05-14"
        st, r = api(co, "POST", "/api/patients", {
            "given_name": "Mariam", "family_name": fam, "birth_date": dob, "gender": "female",
            "national_id": f"{nid[:3]}-{nid[3:7]}-{nid[7:14]}-{nid[14]}", "nationality": "United Arab Emirates",
            "language": "en", "phone": "050 123 4567", "email": "mariam.loop@example.ae", "address_line": "Villa 3, Street 8",
            "city": "Khalifa City", "emirate": "Abu Dhabi", "region": "Abu Dhabi", "preferred_channel": "app",
            "whatsapp": True, "has_smartphone": True, "managing_org_id": 3, "gp_id": None})
        if not ok("Staff registered the patient", st == 200 and "patient_id" in r, status=st, body=r):
            return 1
        pid = r["patient_id"]

        def new_code(channel="in_person", shot=None):
            co.goto(f"{base}/patients/{pid}"); co.wait_for_selector("#invite-form", timeout=20000)
            co.check(f'#invite-form input[value="{channel}"]'); co.click("#invite-form button[type=submit]")
            co.wait_for_selector("#new-code .code-big", timeout=20000)
            if shot:
                co.locator("#sec-account").screenshot(path=str(out / shot))
            return co.inner_text("#new-code .code-big").strip()

        code1 = new_code(shot="patient_staff_account_code_laptop.png")
        uname = q("select username from users where patient_id=?", pid)[0][0]
        ok("App account created with no password until activation", q("select password_hash is null from users where patient_id=?", pid)[0][0] == 1)

        # ---------------------------------------------------------------- activation
        act = ctx_for("pixel"); a = act.new_page(); watch(a, "activate")

        def activate(code, birth, lang="en", password=PATIENT_PW):
            a.goto(f"{base}/activate?lang={lang}"); a.wait_for_selector("#act-form")
            a.fill("#code", code); a.fill("#birth_date", birth)
            if a.locator("#username").count():
                a.fill("#username", uname)
            a.fill("#password", password); a.fill("#confirm", password)
            a.click("#act-form button[type=submit]"); a.wait_for_load_state("networkidle")
            return a.inner_text("#act-error") if a.locator("#act-error").count() else None

        err = activate(code1, "1972-05-15")
        ok("Wrong date of birth refused, code and date kept", err and a.input_value("#code") == code1 and a.input_value("#birth_date") == "1972-05-15", err=err)
        a.screenshot(path=str(out / "patient_activate_error_phone_en.png"))
        ok("Wrong date of birth counted on the invite", q("select failed_attempts from patient_invites where patient_id=? order by id desc", pid)[0][0] == 1)
        # revoke by staff
        co.goto(f"{base}/patients/{pid}"); co.wait_for_selector("#revoke-code", timeout=20000); co.click("#revoke-code")
        co.fill("dialog.dlg[open] textarea", "Given to the wrong person at the desk"); co.click("dialog.dlg[open] [data-ok]"); co.wait_for_timeout(1500)
        ok("Staff cancelled the open code (reason stored)", q("select revoke_reason from patient_invites where patient_id=? order by id desc", pid)[0][0] == "Given to the wrong person at the desk")
        err = activate(code1, dob)
        ok("Cancelled code refused", err is not None and q("select password_hash is null from users where patient_id=?", pid)[0][0] == 1, err=err)
        # expiry
        code2 = new_code("sms")
        q("update patient_invites set expires_at=? where patient_id=? and used_at is null and revoked_at is null",
          (now() - timedelta(minutes=5)).replace(tzinfo=None).isoformat(sep=" "), pid, write=True)
        err = activate(code2, dob)
        ok("Expired code refused", err is not None and "expired" in err.lower(), err=err)
        # lockout
        code3 = new_code("in_person")
        for i in range(5):
            err = activate(code3, f"1972-05-{20 + i}")
        ok("Five wrong dates of birth block the code", q("select revoke_reason from patient_invites where patient_id=? order by id desc", pid)[0][0] == "too many wrong attempts", err=err)
        err = activate(code3, dob)
        ok("Blocked code refused even with the right date", err is not None and q("select password_hash is null from users where patient_id=?", pid)[0][0] == 1, err=err)
        # success in Arabic, then reuse
        code4 = new_code("in_person")
        err = activate(code4, dob, lang="ar")
        body = a.inner_text("body")
        ok("Activation in Arabic succeeds (RTL page)", err is None and uname in body and a.evaluate("() => document.querySelector('.activate-grid').dir") == "rtl", err=err)
        a.screenshot(path=str(out / "patient_activate_done_phone_ar.png"))
        act.close(); act = ctx_for("pixel"); a = act.new_page(); watch(a, "activate2")
        err = activate(code4, dob)
        ok("Used code cannot be used again", err is not None, err=err)
        act.close()
        aud = {e for (e,) in q("select event from audit_events where entity_id=? and event like 'patient.account%'", str(pid))}
        ok("Activation steps audited", {"patient.account_activated", "patient.account_activation_failed"} <= aud, events=sorted(aud))

        # ---------------------------------------------------------------- results
        st1, _ = api(co, "POST", "/api/results", {"patient_id": pid, "programme": "diabetes_hba1c", "value": 5.2, "unit": "%"})
        st2, rr = api(co, "POST", "/api/results", {"patient_id": pid, "programme": "colorectal_fit", "value": 250, "unit": "ug/g"})
        ok("Results received: one normal, one needing follow-up", st1 == 200 and st2 == 200 and rr.get("actionable"), s=(st1, st2))
        cid = rr["case"]["id"]

        # ---------------------------------------------------------------- patient contexts
        P = {"phone_en": login(ctx_for("pixel"), uname, PATIENT_PW, "pixel-en"),
             "phone_ar": login(ctx_for("pixel", "ar-AE"), uname, PATIENT_PW, "pixel-ar"),
             "laptop": login(ctx_for("laptop"), uname, PATIENT_PW, "laptop"),
             "iphone_en": login(ctx_for("iphone"), uname, PATIENT_PW, "iphone-en")}
        LANG = {"phone_en": "en", "phone_ar": "ar", "laptop": "en", "iphone_en": "en"}

        def open_patient(view, lang=None):
            p = P[view]; p.goto(f"{base}/patient?lang={lang or LANG[view]}"); p.wait_for_selector("#ploop .tracker, #ploop .next-box", timeout=20000)
            p.wait_for_selector("#self-details", timeout=20000); p.wait_for_timeout(300)
            return p

        def layout_ok(p, view, where):
            sw, iw = p.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
            ok(f"{where} ({view}): no sideways scrolling", sw <= iw, scroll=sw, width=iw)

        def situation(p):
            return p.get_attribute("#ploop .loop", "data-situation")

        def shots(tag, views=("phone_en", "phone_ar", "laptop"), sections=None):
            for v in views:
                p = open_patient(v)
                layout_ok(p, v, tag)
                for sec, sel in (sections or {"tracker": ".p-hero"}).items():
                    loc = p.locator(sel).first
                    if loc.count():
                        loc.scroll_into_view_if_needed(); loc.screenshot(path=str(out / f"patient_{sec}_{v}.png"))

        p = open_patient("phone_en")
        ok("Patient home: no clinical detail before the care team has told them", situation(p) == "reviewing"
           and "blood" not in p.inner_text("#p-root").lower() and p.locator("#p-about").count() == 0, situation=situation(p))
        ok("Tracker: screening done, result with the care team", p.get_attribute('[data-stage="screened"]', "class").endswith("tk-done")
           and "tk-current" in p.get_attribute('[data-stage="result"]', "class"))
        ok("Results list: follow-up result says the care team is looking at it, normal says normal",
           p.locator('#self-results [data-outcome="reviewing"]').count() == 1 and p.locator('#self-results [data-outcome="normal"]').count() == 1)
        others = p.locator("#ploop details.loop-more").count()
        ok("Second programme (normal result) has its own tracker", others == 1)
        shots("reviewing", sections={"tracker_reviewing": ".p-hero"})

        # notified -> teach-back
        st, _ = api(co, "POST", f"/api/cases/{cid}/notify", {"category": "result_notification"})
        p = open_patient("phone_en")
        ok("After the care team told the patient: plain explanation + questions", st == 200 and situation(p) == "answer"
           and p.locator("#p-about").count() == 1 and p.locator("#tbf").count() == 1, situation=situation(p))
        p.locator("#tb").screenshot(path=str(out / "patient_questions_phone_en.png"))
        p.check('#tbf input[name="q1"][value="a"]'); p.check('#tbf input[name="q2"][value="b"]'); p.check('#tbf input[name="q3"][value="a"]')
        p.check('#tbf input[name="conf"][value="4"]'); p.click("#tbf button[type=submit]"); p.wait_for_timeout(1800)
        ok("Teach-back answered -> case acknowledged", q("select state from cases where id=?", cid)[0][0] == "acknowledged")
        p = open_patient("phone_en")
        ok("Tracker: care team arranging the follow-up check", situation(p) == "arranging", situation=situation(p))

        # pathway to an appointment
        steps = [(gp, f"/api/cases/{cid}/transition", {"to_state": "consultation", "note": "GP reviewed the result"}),
                 (gp, f"/api/cases/{cid}/referral", {"performer_org_id": 9, "performer_id": 31, "summary": "FIT 250 ug/g, no red flags, fit for colonoscopy. Synthetic."}),
                 (sp, f"/api/cases/{cid}/referral/decision", {"accept": True, "note": "accepted"}),
                 (co, f"/api/cases/{cid}/appointment", {"start": (now() + timedelta(days=5)).isoformat()})]
        sts = [api(x, "POST", u, b)[0] for x, u, b in steps]
        ok("Staff referred and booked the appointment", sts == [200] * 4, statuses=sts)
        p = open_patient("phone_en")
        ok("Tracker: appointment booked with confirm / change buttons", situation(p) == "booked"
           and p.locator("#ploop [data-confirm]").count() == 1 and p.locator("#self-appts [data-change]").count() == 1, situation=situation(p))
        appt1 = q("select id from appointments where case_id=? and status='booked'", cid)[0][0]
        p.click("#ploop [data-confirm]"); p.wait_for_timeout(1500)
        ok("Patient confirmed the appointment (stored, audited)", q("select kind from appointment_requests where appointment_id=?", appt1) == [("confirm",)]
           and q("select count(*) from audit_events where event='appointment.patient_confirmed' and entity_id=?", str(appt1))[0][0] == 1)
        p = open_patient("phone_en")
        ok("Tracker shows the confirmation", situation(p) == "booked_confirmed", situation=situation(p))
        # moved by the clinic + reminder
        st_m, _ = api(co, "POST", f"/api/cases/{cid}/appointment/reschedule", {"start": (now() + timedelta(days=8)).isoformat(), "reason_code": "clinic_cancelled"})
        st_r, _ = api(co, "POST", f"/api/cases/{cid}/appointment/remind", {})
        p = open_patient("phone_en")
        txt = p.inner_text("#self-appts")
        ok("Moved appointment shown as moved, with the new time and the reminder", st_m == 200 and st_r == 200
           and p.locator("#self-appts li.moved").count() == 1 and "Reminder sent" in txt and "Moved to" in txt, txt=txt[:400])
        ok("New time needs a new answer", situation(p) == "booked", situation=situation(p))
        shots("booked", sections={"tracker_booked": ".p-hero", "appointments": "#self-appts", "results": "#self-results"})
        # change request
        appt2 = q("select id, start from appointments where case_id=? and status='booked'", cid)[0]
        p.click("#self-appts [data-change]"); p.wait_for_selector(".appt-change")
        p.click(".appt-change button[type=submit]"); p.wait_for_timeout(300)
        ok("Change request needs a reason (inline message)", p.locator(".appt-change .form-error").count() > 0)
        p.check('.appt-change input[name="barrier"][value="work"]'); p.fill(".appt-change input[name=preferred]", "Mornings, not Fridays")
        p.fill(".appt-change textarea[name=note]", "My shift changed this week")
        p.locator(".appt-change").screenshot(path=str(out / "patient_change_request_phone_en.png"))
        P["phone_ar"].goto(f"{base}/patient?lang=ar"); P["phone_ar"].wait_for_selector("#self-appts [data-change]", timeout=20000)
        P["phone_ar"].click("#self-appts [data-change]"); P["phone_ar"].wait_for_selector(".appt-change")
        P["phone_ar"].locator(".appt-change").screenshot(path=str(out / "patient_change_request_phone_ar.png"))
        P["laptop"].goto(f"{base}/patient?lang=en"); P["laptop"].wait_for_selector("#self-appts [data-change]", timeout=20000)
        P["laptop"].click("#self-appts [data-change]"); P["laptop"].wait_for_selector(".appt-change")
        P["laptop"].locator(".appt-change").screenshot(path=str(out / "patient_change_request_laptop.png"))
        p.click(".appt-change button[type=submit]"); p.wait_for_timeout(1800)
        row = q("select kind, barrier_code, preferred_times, note, status from appointment_requests where appointment_id=?", appt2[0])
        ok("Change request stored with barrier, preferred times and note", row == [("change", "work", "Mornings, not Fridays", "My shift changed this week", "open")], row=row)
        ok("Change request did NOT move the appointment", q("select status, start from appointments where id=?", appt2[0]) == [("booked", appt2[1])])
        ok("Barrier recorded as a lesson and audited", q("select count(*) from lessons where case_id=? and kind='appointment_change_request' and reason_code='work'", cid)[0][0] == 1
           and q("select count(*) from audit_events where event='appointment.change_requested' and entity_id=?", str(appt2[0]))[0][0] == 1)
        p = open_patient("phone_en")
        ok("Patient sees the request is with the care team; no second request button", situation(p) == "booked_change"
           and p.locator("#self-appts [data-change]").count() == 0, situation=situation(p))
        # staff see it
        co.goto(f"{base}/queue"); co.wait_for_selector("#search"); co.fill("#search", fam); co.wait_for_timeout(2500)  # this patient's row (a busy queue has many pages)
        ok("Queue flags the patient's change request", "Patient asks to change appointment" in co.inner_text("main"), queue=co.inner_text("main")[:600])
        co.goto(f"{base}/cases/{cid}"); co.wait_for_selector("#patient-requests", timeout=20000)
        ok("Case screen shows the request (barrier, times, note)", all(s in co.inner_text("#patient-requests") for s in ("work or study", "Mornings, not Fridays", "My shift changed this week")))
        co.locator("#patient-requests").screenshot(path=str(out / "patient_request_on_staff_case_laptop.png"))
        # staff cancel at the patient's request -> request handled
        st_c, _ = api(co, "POST", f"/api/cases/{cid}/appointment/cancel", {"reason_code": "patient_request", "barrier_code": "work", "note": "patient asked by app"})
        ok("Staff cancelled at the patient's request; request closed", st_c == 200 and q("select status from appointment_requests where appointment_id=? and kind='change'", appt2[0])[0][0] == "handled")
        p = open_patient("phone_en")
        ok("Tracker back to arranging after the cancellation", situation(p) == "arranging" and p.locator("#self-appts li.cancelled").count() >= 1, situation=situation(p))
        # rebook in the past hour, attend, care plan, close
        sts = [api(co, "POST", f"/api/cases/{cid}/appointment", {"start": (now() - timedelta(hours=1)).isoformat()})[0],
               api(sp, "POST", f"/api/cases/{cid}/attendance", {"attended": True})[0]]
        p = open_patient("phone_en")
        ok("Attended: tracker shows attended, care next", sts == [200, 200] and situation(p) == "attended"
           and p.get_attribute('[data-stage="attended"]', "class").endswith("tk-done"), situation=situation(p))
        sts = [api(sp, "POST", f"/api/cases/{cid}/care-plan", {"kind": "surveillance", "description": "Colonoscopy done; surveillance plan agreed"})[0]]
        p = open_patient("phone_en")
        ok("Care started shown", sts == [200] and situation(p) == "care", situation=situation(p))
        sts = [api(sp, "POST", f"/api/cases/{cid}/close", {"closure_type": "evidence", "evidence_type": "care_plan"})[0]]
        p = open_patient("phone_en")
        rec = q("select due_on from recalls where patient_id=? and programme='colorectal_fit' and status in ('due','invited')", pid)
        ok("Closed: follow-up complete, next screening date shown, nothing urgent left", sts == [200] and situation(p) == "closed"
           and rec and p.locator("#ploop [data-recall]").count() >= 1 and "act within" not in p.inner_text("#p-root").lower()
           and p.locator("#p-about").count() == 0, situation=situation(p), recall=rec)
        shots("closed", sections={"tracker_closed": ".p-hero", "appointments_closed": "#self-appts"})

        # ---------------------------------------------------------------- messages
        p = open_patient("phone_en")
        mt = p.inner_text("#pmsgs-card")
        ok("Messages: every item labelled simulated, no staff call notes, no activation code",
           p.locator("#pmsgs-card .msg-item").count() >= 4 and p.locator("#pmsgs-card .msg-item").count() == p.locator("#pmsgs-card .sim-chip").count()
           and "Phone call:" not in mt and not re.search(r"\b[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}\b", mt), text=mt[:300])
        shots("messages", sections={"messages": "#pmsgs-card"})

        # ---------------------------------------------------------------- contact details
        p.click("#self-edit"); p.fill("#self-form [name=phone]", "050 765 4321"); p.click("#self-form button[type=submit]")
        p.wait_for_selector("#self-edit", timeout=15000)
        ok("Contact details updated and audited", q("select phone from patients where id=?", pid)[0][0] == "+971507654321"
           and q("select count(*) from audit_events where event='patient.self_contact_updated' and entity_id=?", str(pid))[0][0] >= 1)
        # ---------------------------------------------------------------- consent
        p = open_patient("phone_en")
        p.click('#p-consent [data-consent="comm_sms"]'); p.wait_for_timeout(1500)
        ok("Stop SMS: stored as the patient's consent decision and audited",
           q("select decision from consents where patient_id=? and scope='comm_sms' and status='active'", pid) == [("deny",)]
           and q("select count(*) from audit_events where event='consent.recorded' and actor_role='patient'")[0][0] >= 1)
        p = open_patient("phone_en")
        p.click('#p-consent [data-consent="care_coordination"]'); p.wait_for_selector("dialog.dlg[open]")
        p.locator("dialog.dlg[open]").screenshot(path=str(out / "patient_consent_withdraw_dialog_phone_en.png"))
        p.click("dialog.dlg[open] [data-ok]"); p.wait_for_timeout(1500)
        ok("Withdraw care-coordination consent (confirmed, stored)", q("select decision from consents where patient_id=? and scope='care_coordination' and status='active'", pid) == [("deny",)])
        shots("consent", sections={"consent": "#p-consent", "details": "#self-details"})
        p = open_patient("phone_en")
        p.click('#p-consent [data-consent="care_coordination"]'); p.wait_for_timeout(1500)
        p.click('#p-consent [data-consent="comm_sms"]'); p.wait_for_timeout(1500)
        ok("Consent given again", q("select decision from consents where patient_id=? and scope in ('care_coordination','comm_sms') and status='active' order by scope", pid) == [("permit",), ("permit",)])

        # ---------------------------------------------------------------- languages, RTL, devices
        pa = open_patient("phone_ar")
        ok("Arabic: page is right-to-left with Arabic text", pa.evaluate("() => document.getElementById('p-root').dir") == "rtl"
           and "أين أنت الآن" in pa.inner_text("#ploop"))
        box = pa.locator('[data-stage="screened"] .tk-dot').first.bounding_box(); lab = pa.locator('[data-stage="screened"] .tk-label').first.bounding_box()
        ok("Arabic: tracker mirrored (step marker on the right of its label)", box and lab and box["x"] > lab["x"], dot=box, label=lab)
        pa.screenshot(path=str(out / "patient_full_phone_ar.png"), full_page=True)
        for lang, script in (("hi", "[\u0900-\u097f]"), ("ur", "[\u0600-\u06ff]")):
            ph = open_patient("phone_en", lang)
            t = ph.inner_text("#ploop")
            ok(f"{lang}: tracker in {lang} ({'RTL' if lang == 'ur' else 'LTR'})", re.search(script, t) and ph.evaluate("() => document.getElementById('p-root').dir") == ("rtl" if lang == "ur" else "ltr"))
            layout_ok(ph, "phone", f"{lang} page")
            ph.locator(".p-hero").screenshot(path=str(out / f"patient_tracker_closed_phone_{lang}.png"))
        pi = open_patient("iphone_en")
        layout_ok(pi, "iphone_en", "iPhone page")
        pi.screenshot(path=str(out / "patient_full_iphone_en.png"), full_page=True)
        P["laptop"].goto(f"{base}/patient?lang=en"); P["laptop"].wait_for_selector("#self-details"); P["laptop"].wait_for_timeout(500)
        P["laptop"].screenshot(path=str(out / "patient_full_laptop.png"), full_page=True)
        open_patient("phone_en").screenshot(path=str(out / "patient_full_phone_en.png"), full_page=True)
        api(P["phone_en"], "POST", "/api/me/display-language", {"language": "en"})

        # ---------------------------------------------------------------- role separation
        p = P["phone_en"]
        for path in ("/queue", "/patients", f"/cases/{cid}", "/admin/users", "/governance", "/recalls", "/audit"):
            r = p.request.get(base + path, max_redirects=0)
            ok(f"Patient cannot open staff screen {path}", r.status in (302, 303, 403), status=r.status)
        other = q("select a.id from appointments a join cases c on c.id=a.case_id where c.patient_id!=? and a.status='booked' limit 1", pid)
        checks = [("GET", "/api/queue", None), ("GET", "/api/cases", None), ("GET", f"/api/cases/{cid}", None),
                  ("GET", "/api/patients?q=SYN-", None), ("GET", f"/api/patients/{pid}", None), ("GET", "/api/audit", None),
                  ("POST", f"/api/cases/{cid}/appointment/cancel", {"reason_code": "patient_request", "barrier_code": "work"}),
                  ("POST", f"/api/cases/{cid}/close", {"closure_type": "evidence", "evidence_type": "care_plan"})]
        for m, u, b in checks:
            stx, _ = api(p, m, u, b)
            ok(f"Patient refused: {m} {u.split('?')[0]}", stx in (401, 403), status=stx)
        if other:
            stx, _ = api(p, "POST", f"/api/me/appointments/{other[0][0]}/confirm", {})
            ok("Patient cannot answer another patient's appointment", stx == 404, status=stx)
        nav = [x.get_attribute("href") for x in p.locator(".sidebar a, .bottom-nav a").all()]
        ok("Patient navigation has only the patient page", set(nav) <= {"/patient"}, nav=nav)

        # ---------------------------------------------------------------- sign out
        p = open_patient("phone_en")
        p.click("#usermenu summary"); p.click('#usermenu form[action="/logout"] button'); p.wait_for_load_state("networkidle")
        ok("Signed out", "/login" in p.url, url=p.url)
        p.goto(base + "/patient"); p.wait_for_load_state("networkidle")
        ok("After sign out the patient page asks to sign in", "/login" in p.url or p.locator('input[name="password"]').count() == 1, url=p.url)

        ok("No JavaScript errors", not errors, errors=errors[:10])
        br.close()

    bad = [r for r in RES if not r["ok"]]
    (out / "patient_journey_report.json").write_text(json.dumps({"checks": RES, "failed": len(bad)}, ensure_ascii=False, indent=1))
    print(f"\n{len(RES) - len(bad)}/{len(RES)} checks passed; PROBLEMS: {len(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
