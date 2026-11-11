"""End-to-end check of "Forgot username?" / "Forgot password?" in a real browser.

  KARESHAFAA_STAFF_CREDENTIALS_FILE=... python scripts/signin_help_e2e.py http://127.0.0.1:8041 /tmp/pj/k.db OUT_DIR

Same safety rules as patient_journey_e2e.py: a LOCAL production-mode server on a COPY of a SQLite database only
(it creates a patient and requests, and resets a staff password). Codes and passwords are never printed, and they
are masked in screenshots.

Flow: sign-in page links (en / ar, phone and laptop) -> patient: forgot username (match shows it; a wrong detail and
an unknown person get the same answer) -> forgot password (same answer either way) -> coordinator sees the request
in the queue panel and on the record -> must say how the patient was verified -> one-time reset code in person ->
patient sets a new password on /reset-password (Arabic) -> old sessions ended, code single-use -> staff: request
form (validation, sent) -> admin "Sign-in help requests" panel -> find -> reset password (temporary shown once) ->
forced change at sign-in, old session ended -> close the other request with a note -> role checks (GP, clinical
lead, coordinator, admin) and audit. Pixel 7 and laptop. Screenshots: OUT_DIR/signin_help_<screen>_<view>.png.
"""
from __future__ import annotations

import json
import random
import sqlite3
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from patient_journey_e2e import RES, ok, refuse_unsafe, staff_password  # noqa: E402
from kareshafaa.services.patients import emirates_id_check_digit  # noqa: E402

COORD, GP, LEAD, ADMIN, FORGETFUL = "aisha.rahman", "omar.haddad", "fatima.al.mansoori", "admin", "priya.nair"
_D = "0123456789"
PATIENT_PW = "Harbor-Quill-Saffron-" + "".join(random.choice(_D) for _ in range(3))
PATIENT_PW2 = "Meadow-Copper-Lantern-" + "".join(random.choice(_D) for _ in range(3))
STAFF_PW2 = "Orchid-Timber-Compass-" + "".join(random.choice(_D) for _ in range(3))
MASK = "() => document.querySelectorAll('.code-big, #tmp-pw').forEach(e => e.textContent = '••••••')"


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    base, db, out = sys.argv[1].rstrip("/"), sys.argv[2], Path(sys.argv[3])
    refuse_unsafe(base, db)
    staff_pw = staff_password()
    out.mkdir(parents=True, exist_ok=True)

    def q(sql, *a):
        c = sqlite3.connect(db)
        try:
            return c.execute(sql, a).fetchall()
        finally:
            c.close()

    with sync_playwright() as pw:
        br = pw.chromium.launch(channel="chrome")
        errors: list[str] = []

        def ctx_for(view: str, locale="en-GB"):
            if view.startswith("laptop"):
                return br.new_context(viewport={"width": 1366, "height": 900}, locale=locale, timezone_id="Asia/Dubai")
            d = dict(pw.devices["Pixel 7"]); d.pop("default_browser_type", None)
            return br.new_context(**d, locale=locale, timezone_id="Asia/Dubai")

        def watch(p, who):
            p.on("pageerror", lambda e: errors.append(f"{who}: JS {e}"))
            p.on("console", lambda m: m.type == "error" and "status of 4" not in m.text and errors.append(f"{who}: {m.text}"))

        def page(view, who=None, locale="en-GB"):
            p = ctx_for(view, locale).new_page(); watch(p, who or view)
            return p

        def login(view, user, password, who):
            p = page(view, who)
            p.goto(base + "/login"); p.fill("#username", user); p.fill("#password", password)
            p.click("#login-form button[type=submit]"); p.wait_for_load_state("networkidle")
            return p

        def api(p, method, url, body=None):
            tok = p.evaluate("() => (document.querySelector('meta[name=csrf-token]') || {}).content || ''")
            r = p.request.fetch(base + url, method=method, data=json.dumps(body) if body is not None else None,
                                headers={"X-CSRF-Token": tok, "Content-Type": "application/json"})
            try:
                return r.status, r.json()
            except Exception:
                return r.status, r.text()

        def layout_ok(p, view, where):
            sw, iw = p.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
            ok(f"{where} ({view}): no sideways scrolling", sw <= iw, scroll=sw, width=iw)

        def shot(p, name, sel=None, full=False):
            p.evaluate(MASK)
            if sel:
                loc = p.locator(sel).first; loc.scroll_into_view_if_needed(); loc.screenshot(path=str(out / f"signin_help_{name}.png"))
            else:
                p.screenshot(path=str(out / f"signin_help_{name}.png"), full_page=full)

        def direction(p, sel):
            return p.evaluate(f"() => getComputedStyle(document.querySelector('{sel}')).direction")

        def result(p):
            loc = p.locator("#help-result")
            return (loc.get_attribute("data-result"), loc.inner_text().strip()) if loc.count() else (None, "")

        co = login("laptop", COORD, staff_pw, "coordinator")
        gp = login("laptop", GP, staff_pw, "gp")
        lead = login("laptop", LEAD, staff_pw, "lead")
        ok("Staff signed in", all("/login" not in x.url for x in (co, gp, lead)))

        # ---------------------------------------------------------------- sign-in page links
        for view, lang in (("phone", "en"), ("phone", "ar"), ("laptop", "en"), ("laptop", "ar")):
            p = page(view, f"login-{view}-{lang}")
            p.goto(f"{base}/login?lang={lang}"); p.wait_for_selector("#login-form")
            fu, fp = p.locator("#forgot-username"), p.locator("#forgot-password")
            ok(f"Sign-in page shows both links ({view}, {lang})", fu.is_visible() and fp.is_visible()
               and f"lang={lang}" in (fu.get_attribute("href") or ""), u=fu.inner_text() if fu.count() else None)
            ok(f"Sign-in page direction ({view}, {lang})", direction(p, "#login-form") == ("rtl" if lang == "ar" else "ltr"))
            if lang == "ar":
                ok(f"Arabic link text ({view})", any("\u0600" <= ch <= "\u06ff" for ch in fu.inner_text()))
            layout_ok(p, view, f"Sign-in {lang}")
            shot(p, f"login_{view}_{lang}")
            if view == "phone" and lang == "en":
                fu.click(); p.wait_for_selector("#help-form")
                ok("Link opens the forgot-username page", "/forgot-username" in p.url and p.locator("#who-patient").count() == 1)
            p.context.close()

        # ---------------------------------------------------------------- patient + activated app account
        b = "784" + "1981" + f"{random.randint(0, 9_999_999):07d}"
        nid = b + str(emirates_id_check_digit(b))
        fam = "Saeed " + "".join(random.choice("abcdefghijklmnop") for _ in range(6)).capitalize()
        dob, mobile = "1981-06-17", "050 " + "".join(random.choice(_D) for _ in range(3)) + " " + "".join(random.choice(_D) for _ in range(4))
        st, r = api(co, "POST", "/api/patients", {
            "given_name": "Mona", "family_name": fam, "birth_date": dob, "gender": "female",
            "national_id": f"{nid[:3]}-{nid[3:7]}-{nid[7:14]}-{nid[14]}", "nationality": "United Arab Emirates",
            "language": "ar", "phone": mobile, "city": "Abu Dhabi", "emirate": "Abu Dhabi", "region": "Abu Dhabi",
            "preferred_channel": "app", "whatsapp": False, "has_smartphone": True, "managing_org_id": 3, "gp_id": None})
        if not ok("Staff registered the patient", st == 200 and "patient_id" in r, status=st, body=r):
            return 1
        pid = r["patient_id"]
        co.goto(f"{base}/patients/{pid}"); co.wait_for_selector("#invite-form", timeout=20000)
        co.check('#invite-form input[value="in_person"]'); co.click("#invite-form button[type=submit]")
        co.wait_for_selector("#new-code .code-big", timeout=20000)
        code = co.inner_text("#new-code .code-big").strip()
        uname = q("select username from users where patient_id=?", pid)[0][0]
        a = page("phone", "activate"); a.goto(f"{base}/activate?lang=en"); a.wait_for_selector("#act-form")
        a.fill("#code", code); a.fill("#birth_date", dob); a.fill("#password", PATIENT_PW); a.fill("#confirm", PATIENT_PW)
        a.click("#act-form button[type=submit]"); a.wait_for_load_state("networkidle"); a.context.close()
        ok("Patient activated the app account", q("select password_hash is not null from users where patient_id=?", pid)[0][0] == 1)
        old_session = login("phone", uname, PATIENT_PW, "patient-old-session")
        ok("Patient signed in (session to be ended by the reset)", "/patient" in old_session.url, url=old_session.url)
        last4 = nid[-4:]
        wrong4 = f"{(int(last4) + 1) % 10000:04d}"

        def forgot(p, mode, lang, who="patient", via=None, **f):
            url = f"{base}/forgot-{mode}?lang={lang}&who={who}"
            if via:
                url += f"&via={via}"
            p.goto(url); p.wait_for_selector("#help-form")
            for k, v in f.items():
                if k == "id_type":
                    p.check(f'#help-form input[name=id_type][value="{v}"]')
                elif k == "role":
                    p.select_option("#role", v)
                elif k == "need":
                    p.check(f'#help-form input[name=need][value="{v}"]')
                else:
                    p.fill(f"#{k}", v)
            p.click("#help-form button[type=submit]"); p.wait_for_load_state("networkidle")

        # ---------------------------------------------------------------- forgot username
        pe = page("phone", "forgot-en")
        pe.goto(f"{base}/forgot-username?lang=en"); pe.wait_for_selector("#help-form")
        pe.click("#help-form button[type=submit]"); pe.wait_for_timeout(300)
        ok("Forgot username: inline errors when empty (nothing sent)", "/forgot-username" in pe.url and pe.locator('#help-form [aria-invalid="true"]').count() >= 3,
           invalid=pe.locator('#help-form [aria-invalid="true"]').count())
        shot(pe, "username_validation_phone_en", "#help-form")
        forgot(pe, "username", "en", birth_date=dob, id_last4=last4, phone=mobile.replace(" ", ""))
        ok("Forgot username: on a full match the username is shown", result(pe)[0] == "found" and pe.inner_text("#found-username").strip() == uname)
        layout_ok(pe, "phone", "Username found")
        shot(pe, "username_found_phone_en")
        pa = page("phone", "forgot-ar", "ar-AE")
        forgot(pa, "username", "ar", birth_date=dob, id_last4=wrong4, phone=mobile)
        near = result(pa)
        forgot(pa, "username", "ar", birth_date="1950-01-01", id_last4="1234", phone="0509990000")
        nobody = result(pa)
        ok("Forgot username: a wrong detail and an unknown person get the same answer (ar)", near[0] == nobody[0] == "no_match" and near[1] == nobody[1]
           and uname not in pa.content(), near=near, nobody=nobody)
        ok("Forgot username page is right-to-left (ar)", direction(pa, "#help-form") == "rtl")
        layout_ok(pa, "phone", "Forgot username ar")
        shot(pa, "username_generic_phone_ar")
        ok("Typed values are not stored or audited", not q("select id from audit_events where event like 'signin_help.%' and (detail like ? or detail like ?)", f"%{last4}%", f"%{wrong4}%"))

        # ---------------------------------------------------------------- forgot password -> coordinator -> reset
        pl = page("laptop_ar", "forgot-pw-ar", "ar-AE")
        forgot(pl, "password", "ar", birth_date=dob, id_last4=last4, phone=mobile)
        sent = result(pl)
        shot(pl, "password_sent_laptop_ar")
        forgot(pe, "password", "en", birth_date="1950-01-01", id_last4="1234", phone="0509990000")
        forgot(pl, "password", "ar", birth_date="1950-01-02", id_last4="4321", phone="0509990001")
        ok("Forgot password: same answer whether or not the details matched", sent[0] == "sent" and result(pe)[0] == "sent" and result(pl) == sent)
        rq = q("select id, status, audience, kind from signin_help_requests where patient_id=?", pid)
        ok("One open patient reset request stored", len(rq) == 1 and rq[0][1:] == ("open", "patient", "password"), rows=rq)
        hid = rq[0][0] if rq else 0
        co.goto(f"{base}/queue"); co.wait_for_selector(f'#preset [data-reset-request="{hid}"]', timeout=20000)
        ok("Coordinator queue panel lists the reset request", fam in co.inner_text(f'#preset [data-reset-request="{hid}"]'))
        shot(co, "queue_reset_panel_laptop", "#preset")
        co.locator(f'#preset [data-reset-request="{hid}"] a').first.click(); co.wait_for_selector("#reset-request", timeout=20000)
        ok("Patient record shows the reset request in the account section", co.locator("#sec-account #reset-request").is_visible())
        co.check('#invite-form input[value="in_person"]'); co.click("#invite-form button[type=submit]"); co.wait_for_timeout(400)
        ok("Reset code needs how the patient was verified (inline)", co.get_attribute("#verify-method", "aria-invalid") == "true"
           and co.locator("#new-code").count() == 0)
        shot(co, "record_verify_laptop", "#sec-account")
        co.select_option("#verify-method", "in_person_id"); co.click("#invite-form button[type=submit]")
        co.wait_for_selector("#new-code .code-big", timeout=20000)
        rcode = co.inner_text("#new-code .code-big").strip()
        ok("Reset code issued, request marked done with the verify method", q("select status, verify_method from signin_help_requests where id=?", hid) == [("done", "in_person_id")]
           and q("select purpose from patient_invites where patient_id=? order by id desc limit 1", pid) == [("reset",)])
        shot(co, "record_reset_code_laptop", "#sec-account")
        gp.goto(f"{base}/patients/{pid}"); gp.wait_for_selector("#sec-account", timeout=20000)
        ok("GP: no reset form, told coordinators do it", gp.locator("#reset-coord-only").count() == 1 and gp.locator("#verify-method").count() == 0)
        ok("GP cannot issue a reset code (API)", api(gp, "POST", f"/api/patients/{pid}/account-invite", {"channel": "in_person", "verify_method": "in_person_id"})[0] == 403)

        rp = page("phone", "reset-ar", "ar-AE")
        rp.goto(f"{base}/reset-password?lang=ar"); rp.wait_for_selector("#act-form")
        ok("Reset page is right-to-left (ar)", direction(rp, "#act-form") == "rtl")
        layout_ok(rp, "phone", "Reset page ar")
        shot(rp, "reset_form_phone_ar")
        rp.fill("#code", rcode); rp.fill("#birth_date", dob); rp.fill("#password", PATIENT_PW2); rp.fill("#confirm", PATIENT_PW2)
        rp.click("#act-form button[type=submit]"); rp.wait_for_load_state("networkidle")
        ok("Patient set a new password: done page shows the username", rp.locator("[role=status]").count() >= 1 and uname in rp.inner_text("main")
           and q("select count(*) from audit_events where event='patient.password_reset' and entity_id=(select cast(id as text) from users where patient_id=?)", pid)[0][0] == 1, url=rp.url)
        shot(rp, "reset_done_phone_ar")
        old_session.goto(f"{base}/patient"); old_session.wait_for_load_state("networkidle")
        ok("Old patient session ended by the reset", "/login" in old_session.url, url=old_session.url)
        r2 = page("laptop", "reset-reuse"); r2.goto(f"{base}/reset-password?lang=en"); r2.wait_for_selector("#act-form")
        r2.fill("#code", rcode); r2.fill("#birth_date", dob); r2.fill("#password", PATIENT_PW); r2.fill("#confirm", PATIENT_PW)
        r2.click("#act-form button[type=submit]"); r2.wait_for_load_state("networkidle")
        ok("Reset code is single use", "/reset-password" in r2.url and r2.locator("[role=alert]").count() >= 1, url=r2.url)
        shot(r2, "reset_reused_laptop")
        ok("Old password no longer works; new one does", "/login" in login("laptop", uname, PATIENT_PW, "old-pw").url
           and "/patient" in login("laptop", uname, PATIENT_PW2, "new-pw").url)

        # ---------------------------------------------------------------- staff: request -> admin
        fz = login("laptop", FORGETFUL, staff_pw, "forgetful-old-session")
        ok("Staff member signed in (session to be ended by the reset)", "/login" not in fz.url)
        fz_name = q("select display_name from users where username=?", FORGETFUL)[0][0]
        sl = page("laptop", "staff-en")
        sl.goto(f"{base}/forgot-password?lang=en"); sl.wait_for_selector("#who-staff"); sl.click("#who-staff"); sl.wait_for_selector('#help-form[data-who="staff"]')
        ok("Staff password form asks for the username and offers the administrator path",
           sl.locator("#username").count() == 1 and sl.locator("#help-ask-admin").count() == 1
           and "reset link" in sl.inner_text("#staff-intro").lower())
        sl.click("#help-form button[type=submit]"); sl.wait_for_timeout(300)
        ok("Staff email form: inline error when username is empty", sl.locator('#help-form [aria-invalid="true"]').count() >= 1)
        shot(sl, "staff_email_form_validation_laptop", "#help-form")
        sl.click("#help-ask-admin"); sl.wait_for_selector('#help-form[data-path="admin"]')
        ok("Staff are told to contact the administrator", sl.locator("#staff-intro").is_visible() and "administrator" in sl.inner_text("#staff-intro").lower())
        sl.click("#help-form button[type=submit]"); sl.wait_for_timeout(300)
        ok("Staff form: inline errors when empty", sl.locator('#help-form [aria-invalid="true"]').count() >= 4)
        shot(sl, "staff_form_validation_laptop", "#help-form")
        forgot(sl, "password", "en", who="staff", via="admin", full_name=fz_name, role="coordinator", org="Al Ain Health Centre",
               contact="ext 4410", need="password", note="Back from leave, password expired")
        ok("Staff request sent", result(sl)[0] == "staff_sent")
        shot(sl, "staff_sent_laptop")
        sa = page("phone", "staff-ar", "ar-AE")
        forgot(sa, "username", "ar", who="staff", full_name="Hamad Al Ketbi", role="gp", org="Mussafah clinic", contact="hamad@example.ae", need="username")
        ok("Staff request sent (ar, phone)", result(sa)[0] == "staff_sent" and direction(sa, "#help-result") == "rtl")
        layout_ok(sa, "phone", "Staff request ar")
        shot(sa, "staff_sent_phone_ar")
        reqs = {n: i for i, n in q("select id, full_name from signin_help_requests where audience='staff' and status='open'")}
        ad = login("laptop", ADMIN, staff_pw, "admin")
        ad.goto(f"{base}/admin/users"); ad.wait_for_selector("#help-reqs:not([hidden]) [data-help-request]", timeout=20000)
        ok("Admin sees both requests in 'Sign-in help requests'", all(ad.locator(f'[data-help-request="{reqs.get(n, 0)}"]').count() == 1 for n in (fz_name, "Hamad Al Ketbi")), reqs=list(reqs))
        shot(ad, "admin_panel_laptop", "#help-reqs")
        ad.click(f'[data-help-find="{reqs[fz_name]}"]'); ad.wait_for_timeout(800)
        uid = q("select id from users where username=?", FORGETFUL)[0][0]
        ad.click(f'button[onclick="resetPw({uid})"]'); ad.wait_for_selector("dialog.dlg[open]"); ad.click("dialog.dlg[open] [data-ok]")
        ad.wait_for_selector("#tmp-pw", timeout=15000)
        temp = ad.inner_text("#tmp-pw").strip()
        u = q("select must_change_password from users where id=?", uid)[0]
        ok("Admin reset: temporary password shown once, change forced, request closed", u == (1,) and len(temp) >= 12
           and q("select status from signin_help_requests where id=?", reqs[fz_name]) == [("done",)])
        shot(ad, "admin_temp_shown_laptop", "#created")
        ok("Temporary password is not in the audit trail", not q("select id from audit_events where detail like ?", f"%{temp}%"))
        fz.goto(f"{base}/queue"); fz.wait_for_load_state("networkidle")
        ok("Staff member's old session ended", "/login" in fz.url, url=fz.url)
        fp = login("phone", FORGETFUL, temp, "forgetful-new")
        ok("Temporary password forces a change at sign-in", "/account/password" in fp.url, url=fp.url)
        shot(fp, "forced_change_phone")
        fp.fill("#cur", temp); fp.fill("#new", STAFF_PW2); fp.fill("#again", STAFF_PW2)
        fp.click('form[action="/account/password"] button[type=submit]'); fp.wait_for_load_state("networkidle")
        ok("Staff member chose a new password", fp.locator("main [role=status]").count() == 1 and q("select must_change_password from users where id=?", uid)[0] == (0,), url=fp.url)
        ad.goto(f"{base}/admin/users"); ad.wait_for_selector(f'[data-help-close="{reqs["Hamad Al Ketbi"]}"]', timeout=20000)
        ad.click(f'[data-help-close="{reqs["Hamad Al Ketbi"]}"]'); ad.wait_for_selector("dialog.dlg[open] textarea")
        ad.fill("dialog.dlg[open] textarea", "Called back on the clinic number; username given by phone")
        ad.click("dialog.dlg[open] [data-ok]"); ad.wait_for_timeout(1500)
        ok("Admin closed the other request with a note", q("select status, outcome_note from signin_help_requests where id=?", reqs["Hamad Al Ketbi"])
           == [("done", "Called back on the clinic number; username given by phone")])
        adp = login("phone", ADMIN, staff_pw, "admin-phone"); adp.goto(f"{base}/admin/users"); adp.wait_for_selector("#help-reqs:not([hidden])", timeout=20000)
        layout_ok(adp, "phone", "Admin users")
        shot(adp, "admin_panel_phone", "#help-reqs")

        # ---------------------------------------------------------------- role checks + audit
        pu = q("select id from users where patient_id=?", pid)[0][0]
        ok("Admin cannot reset a patient's password", api(ad, "POST", f"/api/admin/users/{pu}/reset-password", {})[0] == 403)
        ok("Admin does not see patient reset requests", api(ad, "GET", "/api/signin-help?audience=patient")[0] == 403)
        ok("Coordinator does not see staff requests", api(co, "GET", "/api/signin-help?audience=staff")[0] == 403)
        ok("Clinical lead sees neither list", api(lead, "GET", "/api/signin-help?audience=staff")[0] == 403 and api(lead, "GET", "/api/signin-help?audience=patient")[0] == 403)
        lead.goto(f"{base}/queue"); lead.wait_for_timeout(2000)
        ok("Clinical lead: no reset panel in the queue", lead.locator("#preset").is_hidden())
        lead.goto(f"{base}/admin/users"); lead.wait_for_load_state("networkidle")
        ok("Clinical lead has no admin tools", lead.locator("#help-reqs").count() == 0, url=lead.url)
        ok("Admin has no clinical actions (cannot issue a patient code)", api(ad, "POST", f"/api/patients/{pid}/account-invite", {"channel": "in_person", "verify_method": "in_person_id"})[0] == 403)
        ev = {e for (e,) in q("select distinct event from audit_events where event like 'signin_help.%' or event in ('patient.password_reset', 'user.password_reset')")}
        ok("Audit trail covers every step", ev >= {"signin_help.patient_username", "signin_help.patient_password", "signin_help.reset_requested", "signin_help.staff_request",
                                                    "patient.password_reset", "user.password_reset"}, events=sorted(ev))
        ok("Codes are not in the audit trail", not q("select id from audit_events where detail like ? or detail like ?", f"%{rcode}%", f"%{code}%"))
        ok("No JavaScript errors", not errors, errors=errors[:10])
        br.close()

    bad = [r for r in RES if not r["ok"]]
    (out / "signin_help_report.json").write_text(json.dumps({"checks": RES, "failed": len(bad)}, ensure_ascii=False, indent=1))
    print(f"\n{len(RES) - len(bad)}/{len(RES)} checks passed; PROBLEMS: {len(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
