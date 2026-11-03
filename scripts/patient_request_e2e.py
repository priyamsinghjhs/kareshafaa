"""End-to-end check of patient appointment REQUESTS in a real browser (patients never book directly).

  KARESHAFAA_STAFF_CREDENTIALS_FILE=... python scripts/patient_request_e2e.py http://127.0.0.1:8041 /tmp/pj/k.db OUT_DIR

Same safety rules as patient_journey_e2e.py: a LOCAL production-mode server on a COPY of a SQLite database only.

Flow: staff register a patient + app account -> activation -> empty appointments show "Request an appointment"
(no dead end) -> screening request form: inline errors, then sent (nothing booked) -> coordinator sees it in the work
queue with its answer-by time -> decline needs a reason -> patient sees the reason (English + Arabic) and a simulated
message -> follow-up result -> referral accepted -> "Choose a time" offers only times before the case deadline ->
patient picks one, withdraws, asks for another time with a barrier -> coordinator confirms a time -> booked ->
recall due -> "Book your next screening" -> coordinator confirms time and site -> audit trail -> role checks
(GP / clinical lead see no request panel, the patient cannot use the coordinator API). Pixel 7 and laptop; en/ar,
hi/ur spot checks. Screenshots: OUT_DIR/patient_request_<screen>_<view>.png.
"""
from __future__ import annotations

import json
import random
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from patient_journey_e2e import RES, ok, refuse_unsafe, staff_password  # noqa: E402
from kareshafaa.services.patients import emirates_id_check_digit  # noqa: E402

COORD, GP, SPEC, LEAD = "aisha.rahman", "omar.haddad", "vikram.mehta", "fatima.al.mansoori"
PATIENT_PW = "Harbor-Quill-Saffron-" + "".join(random.choice("0123456789") for _ in range(3))


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
                return br.new_context(viewport={"width": 1366, "height": 900}, locale=locale, timezone_id="Asia/Dubai")
            d = dict(pw.devices["Pixel 7"]); d.pop("default_browser_type", None)
            return br.new_context(**d, locale=locale, timezone_id="Asia/Dubai")

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
                return r.status, r.json()
            except Exception:
                return r.status, r.text()

        co = login(ctx_for("laptop"), COORD, staff_pw, "coordinator")
        gp = login(ctx_for("laptop"), GP, staff_pw, "gp")
        sp = login(ctx_for("laptop"), SPEC, staff_pw, "specialist")
        lead = login(ctx_for("laptop"), LEAD, staff_pw, "lead")
        ok("Staff signed in", all("/login" not in x.url for x in (co, gp, sp, lead)))

        # ---------------------------------------------------------------- patient + app account
        b = "784" + "1975" + f"{random.randint(0, 9_999_999):07d}"
        nid = b + str(emirates_id_check_digit(b))
        fam = "Nasser " + "".join(random.choice("abcdefghijklmnop") for _ in range(6)).capitalize()
        dob = "1975-03-09"
        st, r = api(co, "POST", "/api/patients", {
            "given_name": "Layla", "family_name": fam, "birth_date": dob, "gender": "female",
            "national_id": f"{nid[:3]}-{nid[3:7]}-{nid[7:14]}-{nid[14]}", "nationality": "United Arab Emirates",
            "language": "ar", "phone": "050 222 3344", "address_line": "Villa 9, Street 2", "city": "Al Ain",
            "emirate": "Abu Dhabi", "region": "Al Ain", "preferred_channel": "app", "whatsapp": False,
            "has_smartphone": True, "managing_org_id": 3, "gp_id": None})
        if not ok("Staff registered the patient", st == 200 and "patient_id" in r, status=st, body=r):
            return 1
        pid = r["patient_id"]
        co.goto(f"{base}/patients/{pid}"); co.wait_for_selector("#invite-form", timeout=20000)
        co.check('#invite-form input[value="in_person"]'); co.click("#invite-form button[type=submit]")
        co.wait_for_selector("#new-code .code-big", timeout=20000)
        code = co.inner_text("#new-code .code-big").strip()
        uname = q("select username from users where patient_id=?", pid)[0][0]
        act = ctx_for("pixel"); a = act.new_page(); watch(a, "activate")
        a.goto(f"{base}/activate?lang=en"); a.wait_for_selector("#act-form")
        a.fill("#code", code); a.fill("#birth_date", dob); a.fill("#password", PATIENT_PW); a.fill("#confirm", PATIENT_PW)
        a.click("#act-form button[type=submit]"); a.wait_for_load_state("networkidle")
        ok("Patient activated the app account", q("select password_hash is not null from users where patient_id=?", pid)[0][0] == 1)
        act.close()

        P = {"phone_en": login(ctx_for("pixel"), uname, PATIENT_PW, "pixel-en"),
             "phone_ar": login(ctx_for("pixel", "ar-AE"), uname, PATIENT_PW, "pixel-ar"),
             "laptop": login(ctx_for("laptop"), uname, PATIENT_PW, "laptop")}
        LANG = {"phone_en": "en", "phone_ar": "ar", "laptop": "en"}

        def open_patient(view, lang=None):
            p = P[view]; p.goto(f"{base}/patient?lang={lang or LANG[view]}")
            p.wait_for_selector("#self-appts", timeout=20000); p.wait_for_timeout(300)
            return p

        def layout_ok(p, view, where):
            sw, iw = p.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
            ok(f"{where} ({view}): no sideways scrolling", sw <= iw, scroll=sw, width=iw)

        def shot(p, sel, name):
            loc = p.locator(sel).first
            loc.scroll_into_view_if_needed(); loc.screenshot(path=str(out / f"patient_request_{name}.png"))

        def requests_of(kind=None):
            return q("select id, kind, status, programme, case_id, recall_id, barrier_code, decline_reason, appointment_id from patient_requests"
                     " where patient_id=?" + (" and kind=?" if kind else "") + " order by id", pid, *([kind] if kind else []))

        # ---------------------------------------------------------------- empty state -> request action
        for v in ("phone_en", "phone_ar", "laptop"):
            p = open_patient(v)
            txt = p.inner_text("#self-appts")
            ok(f"No appointments: a clear 'Request an appointment' action ({v})", p.locator("#self-appts [data-req-new]").count() == 1
               and "You have no appointments yet" not in txt, text=txt[:300])
            layout_ok(p, v, "Empty appointments")
            shot(p, "#self-appts", f"empty_{v}")
        p = open_patient("phone_en")
        p.click("#self-appts [data-req-new]"); p.wait_for_selector("#req-form")
        p.click("#req-form button[type=submit]"); p.wait_for_timeout(300)
        err = p.inner_text("#req-form .form-error") if p.locator("#req-form .form-error").count() else ""
        ok("Request form: inline errors for programme, site, days and times", p.locator('#req-form [aria-invalid="true"]').count() == 4 and err.count(".") >= 4,
           err=err, invalid=p.locator('#req-form [aria-invalid="true"]').count())
        shot(p, "#req-form", "form_validation_phone_en")
        ok("Nothing sent while the form is incomplete", not requests_of())
        p.select_option("#req-form select[name=programme]", "cervical")
        site_val = p.locator("#req-form select[name=site] option").nth(1).get_attribute("value")
        p.select_option("#req-form select[name=site]", site_val)
        p.check('#req-form input[name=days][value="mon"]'); p.check('#req-form input[name=days][value="wed"]')
        p.check('#req-form input[name=times][value="morning"]')
        p.fill("#req-form textarea[name=note]", "Before work if possible")
        shot(p, "#req-form", "form_phone_en")
        pa = open_patient("phone_ar"); pa.click("#self-appts [data-req-new]"); pa.wait_for_selector("#req-form")
        ok("Arabic request form is right-to-left", pa.evaluate("() => document.getElementById('p-root').dir") == "rtl")
        layout_ok(pa, "phone_ar", "Request form")
        shot(pa, "#req-form", "form_phone_ar")
        pl = open_patient("laptop"); pl.click("#self-appts [data-req-new]"); pl.wait_for_selector("#req-form")
        shot(pl, "#req-form", "form_laptop")
        pa = open_patient("phone_ar"); pa.click("#self-appts [data-req-new]"); pa.wait_for_selector("#req-form")
        pa.select_option("#req-form select[name=programme]", "cervical"); pa.select_option("#req-form select[name=site]", site_val)
        pa.check('#req-form input[name=days][value="mon"]'); pa.check('#req-form input[name=days][value="wed"]')
        pa.check('#req-form input[name=times][value="morning"]'); pa.fill("#req-form textarea[name=note]", "Before work if possible")
        pa.click("#req-form button[type=submit]"); pa.wait_for_selector("#self-requests", timeout=15000); pa.wait_for_timeout(500)
        p = open_patient("phone_en")
        rq = requests_of("screening")
        ok("Screening request stored (open), nothing booked", len(rq) == 1 and rq[0][2] == "open"
           and not q("select a.id from appointments a join cases c on c.id=a.case_id where c.patient_id=?", pid), rows=rq)
        ok("Patient sees the request in 'My requests' with its status", p.locator(f'#self-requests [data-request="{rq[0][0]}"][data-status="open"]').count() == 1)
        shot(p, "#self-requests", "sent_phone_en")
        req1 = rq[0][0]
        ok("Request received message (simulated, in the patient's language)", q("select simulated, language from communications where patient_id=? and template_key='appointment_request.received'", pid) == [(1, "ar")])

        # ---------------------------------------------------------------- coordinator: work queue, decline
        co.goto(f"{base}/queue"); co.wait_for_selector(f'#preq [data-request="{req1}"]', timeout=20000)
        row = co.inner_text(f'#preq [data-request="{req1}"]')
        ok("Coordinator work queue lists the request with its answer-by time", fam in row and "reply due" in row and "Cervical" in row, row=row)
        shot(co, "#preq", "queue_laptop")
        co.click(f'#preq [data-rq-decline="{req1}"]'); co.wait_for_selector("#preq-dlg[open]")
        co.click("#preq-ok"); co.wait_for_timeout(300)
        e = co.inner_text("#preq-form .form-error")
        ok("Decline needs a reason and a note (inline)", "reason" in e.lower() and "5 characters" in e, err=e)
        co.select_option("#preq-form select[name=reason]", "no_capacity")
        co.fill("#preq-form textarea[name=note]", "Clinic full this month - we will call you to arrange")
        co.locator("#preq-dlg").screenshot(path=str(out / "patient_request_decline_dialog_laptop.png"))
        co.click("#preq-ok"); co.wait_for_timeout(1500)
        ok("Request declined with reason, audited", q("select status, decline_reason from patient_requests where id=?", req1) == [("declined", "no_capacity")]
           and q("select count(*) from audit_events where event='patient_request.declined' and entity_id=?", str(req1))[0][0] == 1)
        m = q("select content, language, simulated from communications where patient_id=? and template_key='appointment_request.declined'", pid)
        ok("Patient told why, in Arabic (simulated message)", m and m[0][1] == "ar" and m[0][2] == 1 and "[" in m[0][0], msg=m)
        for v in ("phone_en", "phone_ar"):
            p = open_patient(v)
            ok(f"Patient sees the decline and its reason ({v})", p.locator(f'#self-requests [data-request="{req1}"][data-status="declined"]').count() == 1)
            shot(p, "#self-requests", f"declined_{v}")

        # ---------------------------------------------------------------- follow-up: choose a time
        st2, rr = api(co, "POST", "/api/results", {"patient_id": pid, "programme": "colorectal_fit", "value": 250, "unit": "ug/g"})
        cid = rr["case"]["id"]
        api(co, "POST", f"/api/cases/{cid}/notify", {"category": "result_notification"})
        p = open_patient("phone_en"); p.wait_for_selector("#tbf", timeout=20000)
        p.check('#tbf input[name="q1"][value="a"]'); p.check('#tbf input[name="q2"][value="b"]'); p.check('#tbf input[name="q3"][value="a"]')
        p.check('#tbf input[name="conf"][value="4"]'); p.click("#tbf button[type=submit]"); p.wait_for_timeout(1800)
        p = open_patient("phone_en")
        ok("Before the referral is accepted there is no 'Choose a time'", p.locator("#ploop [data-pick]").count() == 0)
        sts = [api(gp, "POST", f"/api/cases/{cid}/transition", {"to_state": "consultation", "note": "GP reviewed the result"})[0],
               api(gp, "POST", f"/api/cases/{cid}/referral", {"performer_org_id": 9, "performer_id": 31, "summary": "FIT 250 ug/g, no red flags. Synthetic."})[0],
               api(sp, "POST", f"/api/cases/{cid}/referral/decision", {"accept": True, "note": "accepted"})[0]]
        ok("Referral accepted (follow-up triaged)", sts == [200] * 3, statuses=sts)
        st, prof = api(P["phone_en"], "GET", "/api/me/profile?lang=en")
        lp = next(x for x in prof["loop"] if x.get("case_id") == cid)
        ct = lp["choose_time"]
        deadline = datetime.fromisoformat(ct["deadline"].replace("Z", "+00:00"))
        sl = [datetime.fromisoformat(s.replace("Z", "+00:00")) for s in ct["slots"]]
        tier = q("select tier from cases where id=?", cid)[0][0]
        ok(f"Offered times are all inside the case deadline ({ct['tier_label']}, {tier})", sl and all(now() < s <= deadline for s in sl), deadline=ct["deadline"], slots=ct["slots"][:3])
        for v in ("phone_en", "phone_ar", "laptop"):
            p = open_patient(v)
            p.click("#ploop [data-pick]"); p.wait_for_selector("#pick-form")
            ok(f"'Choose a time' shows the offered times ({v})", p.locator("#pick-form input[name=slot]").count() == len(sl))
            layout_ok(p, v, "Choose a time")
            shot(p, "#pick-form", f"choose_time_{v}")
        p = open_patient("phone_en"); p.click("#ploop [data-pick]"); p.wait_for_selector("#pick-form")
        p.click("#pick-form button[type=submit]"); p.wait_for_timeout(300)
        ok("Choose a time: inline error when nothing picked", p.locator("#pick-form .form-error").count() > 0)
        p.locator("#pick-form input[name=slot]").first.check(); p.click("#pick-form button[type=submit]"); p.wait_for_timeout(1800)
        fu = requests_of("follow_up")
        ok("Picked time sent as a request (not booked)", len(fu) == 1 and fu[0][2] == "open" and q("select state from cases where id=?", cid)[0][0] == "referral_accepted", rows=fu)
        p = open_patient("phone_en")
        ok("Tracker says the request is with the care team; no second 'Choose a time'", p.locator("#ploop [data-open-request]").count() >= 1 and p.locator("#ploop [data-pick]").count() == 0)
        p.once("dialog", lambda d: d.accept())
        p.click(f'#self-requests [data-withdraw="{fu[0][0]}"]'); p.wait_for_selector("dialog.dlg[open]", timeout=5000); p.click("dialog.dlg[open] [data-ok]"); p.wait_for_timeout(1500)
        ok("Patient withdrew the request (audited)", q("select status from patient_requests where id=?", fu[0][0]) == [("withdrawn",)]
           and q("select count(*) from audit_events where event='patient_request.withdrawn' and entity_id=?", str(fu[0][0]))[0][0] == 1)
        p = open_patient("phone_en"); p.click("#ploop [data-pick]"); p.wait_for_selector("#pick-form")
        p.check("#pick-form input[name=other]"); p.wait_for_selector("#pick-form [data-other]:not([hidden])")
        p.click("#pick-form button[type=submit]"); p.wait_for_timeout(300)
        ok("Asking for another time needs a reason (inline)", p.locator("#pick-form .form-error").count() > 0)
        p.check('#pick-form input[name=barrier][value="childcare"]'); p.fill("#pick-form input[name=preferred]", "After 3pm, not Thursdays")
        p.fill("#pick-form textarea[name=note]", "School pick-up")
        shot(p, "#pick-form", "change_phone_en")
        pa = open_patient("phone_ar"); pa.click("#ploop [data-pick]"); pa.wait_for_selector("#pick-form"); pa.check("#pick-form input[name=other]")
        pa.wait_for_selector("#pick-form [data-other]:not([hidden])"); shot(pa, "#pick-form", "change_phone_ar")
        p.click("#pick-form button[type=submit]"); p.wait_for_timeout(1800)
        fu2 = requests_of("follow_up")[-1]
        ok("Change request stores the barrier (and a lesson)", fu2[2] == "open" and fu2[6] == "childcare"
           and q("select count(*) from lessons where case_id=? and kind='appointment_change_request' and reason_code='childcare'", cid)[0][0] == 1, row=fu2)
        # coordinator confirms with a time inside the deadline
        co.goto(f"{base}/queue"); co.wait_for_selector(f'#preq [data-request="{fu2[0]}"]', timeout=20000)
        ok("Follow-up request in the work queue with its T-tier", "T" in co.inner_text(f'#preq [data-request="{fu2[0]}"] .chips'))
        co.click(f'#preq [data-rq-confirm="{fu2[0]}"]'); co.wait_for_selector("#preq-dlg[open]")
        co.click("#preq-ok"); co.wait_for_timeout(300)
        ok("Confirm needs a time when the patient asked for another one", "time" in co.inner_text("#preq-form .form-error").lower())
        local = (sl[-1] + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M")
        co.fill("#preq-form input[name=start]", local)
        co.locator("#preq-dlg").screenshot(path=str(out / "patient_request_confirm_dialog_laptop.png"))
        co.click("#preq-ok"); co.wait_for_timeout(2000)
        row = q("select status, appointment_id from patient_requests where id=?", fu2[0])[0]
        ok("Coordinator confirmed: appointment booked, case booked, audited", row[0] == "confirmed" and row[1]
           and q("select state from cases where id=?", cid)[0][0] == "appointment_booked"
           and q("select count(*) from audit_events where event='patient_request.confirmed' and entity_id=?", str(fu2[0]))[0][0] == 1, row=row)
        p = open_patient("phone_en")
        ok("Patient sees the booked appointment and the confirmed request", p.get_attribute("#ploop .loop", "data-situation") == "booked"
           and p.locator(f'#self-requests [data-request="{fu2[0]}"][data-status="confirmed"]').count() == 1)
        shot(p, "#self-requests", "confirmed_phone_en")

        # ---------------------------------------------------------------- recall due -> book your next screening
        api(co, "POST", "/api/results", {"patient_id": pid, "programme": "diabetes_hba1c", "value": 5.2, "unit": "%"})
        due = (now() + timedelta(days=20)).date().isoformat()
        q("insert into recalls (patient_id, programme, due_on, reason, interval_months, status, created_at, created_by, invite_count)"
          " values (?, 'diabetes_hba1c', ?, 'Routine re-screening', 36, 'due', ?, 'e2e', 0)", pid, due, now().replace(tzinfo=None).isoformat(sep=" "), write=True)
        rec_id = q("select id from recalls where patient_id=? and programme='diabetes_hba1c' order by id desc", pid)[0][0]
        for v in ("phone_en", "phone_ar"):
            p = open_patient(v)
            ok(f"Recall due: 'Book your next screening' shown ({v})", p.locator(f'#ploop [data-book-recall="{rec_id}"]').count() == 1)
        p = open_patient("phone_en")
        btn = p.locator(f'#ploop [data-book-recall="{rec_id}"]')
        ok("A waiting action keeps its programme open (not hidden in a closed section)", btn.is_visible())
        btn.click(); p.wait_for_selector("#req-form")
        ok("Recall form: programme fixed to the reminder", p.locator("#req-form select[name=programme][disabled]").count() == 1
           and p.input_value("#req-form select[name=programme]") == "diabetes_hba1c")
        p.select_option("#req-form select[name=site]", site_val)
        p.check('#req-form input[name=days][value="tue"]'); p.check('#req-form input[name=times][value="afternoon"]')
        shot(p, "#req-form", "recall_form_phone_en")
        p.click("#req-form button[type=submit]"); p.wait_for_timeout(1800)
        rc = requests_of("recall")
        ok("Recall request linked to the recall", len(rc) == 1 and rc[0][5] == rec_id and rc[0][2] == "open", rows=rc)
        co.goto(f"{base}/queue"); co.wait_for_selector(f'#preq [data-request="{rc[0][0]}"]', timeout=20000)
        co.click(f'#preq [data-rq-confirm="{rc[0][0]}"]'); co.wait_for_selector("#preq-dlg[open]")
        when = (now() + timedelta(days=14)).astimezone(timezone(timedelta(hours=4))).replace(hour=10, minute=0)
        co.fill("#preq-form input[name=start]", when.strftime("%Y-%m-%dT%H:%M"))
        co.click("#preq-ok"); co.wait_for_timeout(1800)
        ok("Recall request confirmed (time + site), recall marked invited", q("select status, confirmed_org_id from patient_requests where id=?", rc[0][0]) == [("confirmed", int(site_val))]
           and q("select status from recalls where id=?", rec_id) == [("invited",)])
        p = open_patient("phone_ar")
        shot(p, "#self-requests", "list_phone_ar")
        p.screenshot(path=str(out / "patient_request_full_phone_ar.png"), full_page=True)
        open_patient("laptop").screenshot(path=str(out / "patient_request_full_laptop.png"), full_page=True)
        msgs = q("select template_key, language from communications where patient_id=? and category='appointment_request' order by id", pid)
        lm = q("select count(*) from communications where patient_id=? and category='appointment_request' and language not in ('en', 'ar')", pid)[0][0]
        ok("Every step messaged (simulated, in the language the patient used)", [k for k, _ in msgs].count("appointment_request.received") == 4
           and [k for k, _ in msgs].count("appointment_request.confirmed") == 2 and [k for k, _ in msgs].count("appointment_request.declined") == 1
           and lm == 0 and q("select count(*) from communications where patient_id=? and category='appointment_request' and simulated=0", pid)[0][0] == 0, msgs=msgs)
        pm = open_patient("phone_en")
        ok("Request messages appear in the patient's messages", pm.locator("#pmsgs-card .msg-item").count() >= 6)
        for lang, script in (("hi", "[\u0900-\u097f]"), ("ur", "[\u0600-\u06ff]")):
            ph = open_patient("phone_en", lang)
            ok(f"{lang}: requests list in {lang}", re.search(script, ph.inner_text("#self-requests")) is not None)
        api(P["phone_en"], "POST", "/api/me/display-language", {"language": "ar"})

        # ---------------------------------------------------------------- audit + role checks
        ev = {e for (e,) in q("select distinct event from audit_events where entity_type='PatientRequest' and entity_id in (select cast(id as text) from patient_requests where patient_id=?)", pid)}
        ok("Audit trail: created, withdrawn, confirmed, declined", ev >= {"patient_request.created", "patient_request.withdrawn", "patient_request.confirmed", "patient_request.declined"}, events=sorted(ev))
        for who, pg in (("gp", gp), ("clinical lead", lead)):
            pg.goto(f"{base}/queue"); pg.wait_for_timeout(2500)
            ok(f"{who}: no patient-request panel, cannot decide", pg.locator("#preq").is_hidden()
               and api(pg, "POST", f"/api/patient-requests/{rc[0][0]}/decline", {"reason_code": "other", "note": "not mine"})[0] == 403)
        lead.goto(f"{base}/admin/users"); lead.wait_for_load_state("networkidle")
        ok("Clinical lead still has no admin tools", "/admin/users" not in lead.url or lead.locator("#f-staff").count() == 0, url=lead.url)
        pp = P["phone_en"]
        ok("Patient cannot use the coordinator work list", api(pp, "GET", "/api/patient-requests")[0] == 403)
        other_case = q("select id from cases where patient_id!=? and state='referral_accepted' limit 1", pid)
        if other_case:
            ok("Patient cannot request for someone else's case", api(pp, "POST", "/api/me/requests/follow-up", {"case_id": other_case[0][0], "barrier_code": "work"})[0] == 404)
        other_rec = q("select id from recalls where patient_id!=? and status='due' limit 1", pid)
        if other_rec:
            ok("Patient cannot use someone else's recall", api(pp, "POST", "/api/me/requests/screening", {"programme": "cervical", "site_org_id": int(site_val), "days": ["mon"], "times": ["morning"], "recall_id": other_rec[0][0]})[0] in (404, 422))
        ok("Staff cannot send a patient request", api(co, "POST", "/api/me/requests/screening", {"programme": "cervical", "site_org_id": int(site_val), "days": ["mon"], "times": ["morning"]})[0] == 403)
        ok("No JavaScript errors", not errors, errors=errors[:10])
        br.close()

    bad = [r for r in RES if not r["ok"]]
    (out / "patient_request_report.json").write_text(json.dumps({"checks": RES, "failed": len(bad)}, ensure_ascii=False, indent=1))
    print(f"\n{len(RES) - len(bad)}/{len(RES)} checks passed; PROBLEMS: {len(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
