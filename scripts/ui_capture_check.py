"""Browser data-capture check: fill every form like a user, then check the DB and the reloaded page.

Run against a throwaway server on a COPY of a SQLite database (never a live database):
  KARESHAFAA_DEMO_CREDENTIALS_FILE=... python scripts/ui_capture_check.py http://127.0.0.1:8013 /tmp/cap/kareshafaa.db /tmp/cap/out desktop|mobile
Each field gets a unique Unicode token; the report lists where it was stored, whether the reloaded page shows it,
and any HTTP/JS errors. Exits non-zero when anything was lost. Passwords come from the credentials file or
KARESHAFAA_DEMO_PASSWORD, never argv.
"""
import os, json, random, re, sqlite3, sys, time, traceback
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8013"
DB = sys.argv[2] if len(sys.argv) > 2 else "/tmp/cap/kareshafaa.db"
OUT = Path(sys.argv[3] if len(sys.argv) > 3 else "/tmp/cap/out"); OUT.mkdir(parents=True, exist_ok=True)
VIEW = sys.argv[4] if len(sys.argv) > 4 else "desktop"
_CF = os.environ.get("KARESHAFAA_DEMO_CREDENTIALS_FILE")
PW = next((l.split("\t")[2] for l in Path(_CF).read_text().splitlines()
           if l and not l.startswith("#") and len(l.split("\t")) == 3), None) if _CF and Path(_CF).exists() else os.environ["KARESHAFAA_DEMO_PASSWORD"]
RUN = f"{random.randint(1000, 9999)}"
VIEWS = {"mobile": dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True),
         "desktop": dict(viewport={"width": 1366, "height": 900})}
RES = []


def tok(name):
    return f"T{RUN}{name.replace('.', '').replace('_', '')}X ملاحظة é"


def key(t):
    return t.split(" ")[0]


def db_hits(t):
    c = sqlite3.connect(DB)
    out = []
    for (tb,) in c.execute("select name from sqlite_master where type='table'"):
        for col in c.execute(f"pragma table_info({tb})"):
            name, typ = col[1], (col[2] or "").upper()
            if any(x in typ for x in ("CHAR", "TEXT", "JSON", "CLOB")) or typ == "":
                v = c.execute(f'select CAST("{name}" AS TEXT) from "{tb}" where CAST("{name}" AS TEXT) like ? limit 1', (f"%{key(t)}%",)).fetchone()
                if v:
                    raw = v[0] or ""
                    try:  # JSON columns keep non-ASCII as \uXXXX escapes: decode before comparing
                        dec = json.dumps(json.loads(raw), ensure_ascii=False) if typ == "JSON" else raw
                    except ValueError:
                        dec = raw
                    out.append((f"{tb}.{name}", t in raw or t in dec))
    c.close()
    return out


def q1(sql, *a):
    c = sqlite3.connect(DB); r = c.execute(sql, a).fetchone(); c.close(); return r


class Browser:
    def __init__(self, pw, user):
        self.ctx = pw.ctx_browser.new_context(**VIEWS[VIEW], locale="en-GB", timezone_id="Asia/Dubai")
        self.p = self.ctx.new_page(); self.errs = []; self.user = user
        self.p.on("pageerror", lambda e: self.errs.append("JS " + str(e)))
        self.p.on("response", lambda r: self.errs.append(f"{r.status} {r.request.method} {r.url}") if r.status >= 400 and "/api/" in r.url else None)
        self.p.goto(BASE + "/login"); self.p.fill('input[name="username"]', user); self.p.fill('input[name="password"]', PW)
        self.p.click('button[type="submit"]'); self.p.wait_for_load_state("networkidle")
        assert "/login" not in self.p.url, f"login failed for {user}"

    def case(self, cid):
        self.p.goto(f"{BASE}/cases/{cid}"); self.p.wait_for_selector("#case-root .case-head", timeout=20000); self.p.wait_for_timeout(300)

    def action(self, k):
        b = self.p.locator(f'#case-root aside [data-k="{k}"]')
        if not b.count():
            raise AssertionError(f"action {k} not offered; have " + ",".join(self.p.eval_on_selector_all("#case-root aside [data-k]", "e=>e.map(x=>x.dataset.k)")))
        more = self.p.locator("details.more-actions")
        if more.count() and not more.evaluate("d=>d.open") and b.first.evaluate("b=>!!b.closest('details')"):
            more.locator("summary").click()
        b.first.click(); self.p.wait_for_selector("#aform form, .dlg[open]", timeout=8000)

    def submit_form(self, sel="#aform form"):
        f = self.p.locator(sel)
        f.locator("button[type=submit]").click()
        if self.p.locator("dialog.dlg[open]").count():  # risky-action confirmation
            self.p.locator("dialog.dlg[open] [data-ok]").click()
        self.p.wait_for_function(f"() => !document.querySelector('{sel}') || document.querySelector('{sel} .form-error')", timeout=15000)
        err = self.p.locator(f"{sel} .form-error")
        return err.inner_text() if err.count() else None

    def case_text(self):
        p = self.p
        p.evaluate("() => document.querySelectorAll('#case-root details').forEach(d => d.open = true)")
        t = p.inner_text("#case-root")
        for tab in ("events", "audit"):
            b = p.locator(f'.tabs button[data-t="{tab}"]')
            if b.count():
                b.click(); p.wait_for_timeout(150); t += "\n" + p.inner_text("#tabbody")
        return t

    def shot(self, name):
        self.p.screenshot(path=str(OUT / f"{VIEW}_{name}.png"), full_page=False)


def record(field, page, t, err=None, shown_text=None, extra=None):
    hits = db_hits(t) if t else []
    dom = [h for h in hits if not h[0].startswith("audit_events")]
    RES.append({"field": field, "page": page, "error": err, "db": [h[0] for h in dom], "unicode_ok": all(h[1] for h in dom) if dom else None,
                "audit_only": bool(hits) and not dom, "shown_after_reload": (t in shown_text) if (t and shown_text is not None) else None, **(extra or {})})


def step(name, fn):
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        RES.append({"field": name, "error": "EXCEPTION " + str(e)[:300], "trace": traceback.format_exc()[-600:]})


def hl7_for(pid, value=250, birth=None):
    mrn, fam, giv, by, sex = q1("select mrn, family_name, given_name, birth_year, gender from patients where id=?", pid)
    ts = time.strftime("%Y%m%d%H%M%S", time.gmtime()) + "+0000"
    return "\n".join([f"MSH|^~\\&|LIS|CENTRAL_SCREENING_LAB|KARESHAFAA|EMIRATE_SCREENING|{ts}||ORU^R01^ORU_R01|LAB-{RUN}-{pid}-{VIEW}|P|2.5.1",
                      f"PID|1||{mrn}^^^KARESHAFAA^MR||{fam.upper()}^{giv.upper()}||{by}0101|{sex[0].upper()}",
                      f"OBR|1|ORD-{pid}|FIL-{ts[-6:]}|29771-3^FIT haemoglobin^LN|||{ts}|||||||||||||||{ts}|||F",
                      f"OBX|1|NM|29771-3^FIT haemoglobin^LN||{value}|ug/g|<120|H|||F"])


def run():
    with sync_playwright() as pw:
        pw.ctx_browser = pw.chromium.launch(channel="chrome", headless=True)
        S = {}
        pids = [r[0] for r in sqlite3.connect(DB).execute("""select p.id from patients p where p.managing_org_id=3 and p.gp_id=19 and p.deceased=0
            and p.birth_year between 1956 and 1972 and p.language in ('en','ar') and not exists(select 1 from cases c where c.patient_id=p.id and c.state!='closed')
            and not exists(select 1 from consents k where k.patient_id=p.id and k.decision='deny') order by p.id limit 3""")]
        co = Browser(pw, "aisha.rahman")

        def ingest():  # Results inbox: hand-entered HL7 v2 result -> new case
            p = co.p; p.goto(BASE + "/ingest"); p.wait_for_selector("#recent")
            p.locator("details.card.flat summary").click()
            for i, pid in enumerate(pids):
                p.fill("#msg", hl7_for(pid)); p.click("button:has-text('Add result')"); p.wait_for_selector("#ack:not([hidden])", timeout=15000)
                p.wait_for_timeout(300)
                row = q1("select id, state from cases where patient_id=? and state!='closed' order by id desc", pid)
                S[f"case{i}"] = row[0] if row else None
            RES.append({"field": "ingest.hl7 (Results inbox form)", "page": "/ingest", "cases": [S.get(f"case{i}") for i in range(3)], "errors": co.errs[-3:]})
            co.shot("ingest")
        step("ingest", ingest)
        cid = S.get("case0"); S["cid"] = cid

        def review_if_needed():
            for i in range(3):
                c = S.get(f"case{i}")
                if c and q1("select review_required from cases where id=?", c)[0]:
                    gp0 = Browser(pw, "omar.haddad"); gp0.case(c); gp0.action("review"); t = tok(f"review.note{i}") + " result reviewed"
                    gp0.p.select_option("#aform select[name=tier]", "urgent"); gp0.p.fill("#aform textarea[name=note]", t)
                    err = gp0.submit_form(); gp0.case(c); record("Clinical review: note + urgency", f"/cases/{c} (GP)", t, err, gp0.case_text(),
                                                               {"tier_saved": q1("select tier, review_required from cases where id=?", c)})
                    gp0.ctx.close()
        step("review", review_if_needed)

        def coord_flow():
            co.case(cid)
            if "notify" in co.p.eval_on_selector_all("#case-root aside [data-k]", "e=>e.map(x=>x.dataset.k)"):
                co.p.locator('#case-root aside [data-k="notify"]').first.click(); co.p.wait_for_timeout(1200)
            co.case(cid); co.action("call")
            t = tok("call.note"); co.p.select_option("#aform select[name=outcome]", "reached"); co.p.fill("#aform textarea[name=note]", t)
            err = co.submit_form(); co.case(cid); record("Call outcome + note", f"/cases/{cid} (coordinator)", t, err, co.case_text())
            co.action("teachback")
            for qn, v in (("q1", "a"), ("q2", "b"), ("q3", "a")):
                co.p.check(f'#aform input[name="{qn}"][value="{v}"]')
            co.p.select_option("#aform select[name=confidence]", "4")
            err = co.submit_form(); r = q1("select confidence, answers, language from questionnaire_responses where case_id=? order by id desc", cid)
            RES.append({"field": "Navigator teach-back (answers, confidence)", "page": f"/cases/{cid}", "error": err, "db_row": r,
                        "ok": bool(r and r[0] == 4 and '"q2": "b"' in (r[1] or "").replace("'", '"'))})
            co.case(cid); co.action("lesson")
            t = tok("lesson.detail"); co.p.select_option("#aform select[name=kind]", "incident"); co.p.fill("#aform input[name=detail]", t)
            err = co.submit_form(); co.case(cid); record("Lesson learned (kind, reason, detail)", f"/cases/{cid}", t, err, co.case_text(),
                                                     {"kind_saved": q1("select kind from lessons where detail like ?", f"%{key(t)}%")})
            co.action("consent")
            t = tok("consent.note"); co.p.select_option("#aform select[name=scope]", "comm_whatsapp"); co.p.select_option("#aform select[name=decision]", "permit")
            co.p.fill("#aform textarea[name=note]", t); err = co.submit_form(); co.case(cid)
            record("Consent (scope, decision, note)", f"/cases/{cid}", t, err, co.case_text(), {"scope_saved": q1("select scope, decision from consents where note like ?", f"%{key(t)}%")})
            co.shot("case_coordinator_after_saves")
        step("coordinator flow", coord_flow)

        gp = Browser(pw, "omar.haddad")

        def gp_flow():
            gp.case(cid); gp.action("consultation")
            t = tok("transition.note"); gp.p.fill("#aform textarea[name=note]", t); err = gp.submit_form(); gp.case(cid)
            record("Consultation step note", f"/cases/{cid} (GP)", t, err, gp.case_text())
            # clinical record forms
            gp.p.click('[data-rec="encounter"]'); f = "#rec-form-encounter form"
            t = tok("enc.notes"); t2 = tok("enc.reason"); t3 = tok("enc.outcome")
            gp.p.select_option(f + " select[name=kind]", "gp_visit"); gp.p.fill(f + " input[name=reason]", t2); gp.p.fill(f + " textarea[name=notes]", t); gp.p.fill(f + " input[name=outcome]", t3)
            err = gp.submit_form(f); gp.case(cid); txt = gp.case_text()
            for lab, x in (("notes", t), ("reason", t2), ("outcome", t3)):
                record(f"Consultation note: {lab}", f"/cases/{cid} record", x, err, txt)
            gp.p.click('[data-rec="medication"]'); f = "#rec-form-medication form"
            m = {"name": tok("med.name"), "dose": "500 mg", "frequency": tok("med.freq"), "route": "oral", "reason": tok("med.reason"), "note": tok("med.note")}
            for k2 in ("name", "dose", "frequency", "route", "reason"):
                gp.p.fill(f"{f} input[name={k2}]", m[k2])
            gp.p.fill(f + " textarea[name=note]", m["note"]); gp.p.fill(f + " input[name=start_date]", "2026-09-01")
            if gp.p.locator(f + " input[name=end_date]").count():
                gp.p.fill(f + " input[name=end_date]", "2026-12-31")
            err = gp.submit_form(f); gp.case(cid); txt = gp.case_text()
            for k2 in ("name", "frequency", "reason", "note"):
                record(f"Medicine: {k2}", f"/cases/{cid} record", m[k2], err, txt)
            row = q1("select dose, route, start_date, end_date from medication_requests where name like ?", f"%{key(m['name'])}%")
            RES.append({"field": "Medicine: dose/route/start/end dates", "db_row": row, "ok": bool(row and row[0] == "500 mg" and row[2] and row[2].startswith("2026-09-01") and row[3] and row[3].startswith("2026-12-31"))})
            gp.p.click(f'[data-stop]'); t = tok("med.stop")
            gp.p.fill("dialog.dlg[open] textarea", t); gp.p.click("dialog.dlg[open] [data-ok]"); gp.p.wait_for_selector("dialog.dlg[open]", state="detached", timeout=10000)
            gp.case(cid); record("Stop medicine: reason", f"/cases/{cid} record", t, None, gp.case_text())
            gp.action("consult"); t = tok("consult.q")
            gp.p.select_option("#aform select[name=target]", "31"); gp.p.select_option("#aform select[name=urgency]", "high"); gp.p.fill("#aform textarea[name=question]", t)
            err = gp.submit_form(); gp.case(cid); record("Ask a specialist: question", f"/cases/{cid}", t, err, gp.case_text(),
                                                         {"urgency_saved": q1("select urgency, target_practitioner_id from consult_requests where question like ?", f"%{key(t)}%")})
            gp.action("refer"); t = tok("ref.summary")
            gp.p.select_option("#aform select[name=org]", "9"); gp.p.select_option("#aform select[name=spec]", "31"); gp.p.fill("#aform textarea[name=summary]", t)
            err = gp.submit_form(); gp.case(cid); record("Referral: summary", f"/cases/{cid}", t, err, gp.case_text(),
                                                         {"to_saved": q1("select performer_org_id, performer_id from service_requests where clinical_summary like ?", f"%{key(t)}%")})
            gp.shot("case_gp_record_saved")
        step("gp flow", gp_flow)

        sp = Browser(pw, "vikram.mehta")

        def sp_flow():
            sp.case(cid); sp.p.click('.tabs button[data-t="events"]'); t = tok("consult.answer")
            ta = sp.p.locator('#tabbody textarea[id^="resp-"]').first; ta.fill(t); ta.locator("xpath=following-sibling::button").click()
            sp.p.wait_for_timeout(1500); sp.case(cid); record("Answer to e-consult (case timeline)", f"/cases/{cid} (specialist)", t, None, sp.case_text())
            sp.action("decide_referral"); t = tok("ref.decision")
            sp.p.select_option("#aform select[name=accept]", "true"); sp.p.fill("#aform textarea[name=note]", t)
            err = sp.submit_form(); sp.case(cid); record("Referral decision: note", f"/cases/{cid}", t, err, sp.case_text())
        step("specialist flow", sp_flow)

        def booking():
            co.case(cid); co.action("book"); t = tok("book.note")
            co.p.fill("#aform input[name=beyond]", t)
            when = q1("select datetime('now','+2 hours')")[0].replace(" ", "T")[:16]
            co.p.fill("#aform input[name=start]", when)
            err = co.submit_form(); co.case(cid); txt = co.case_text()
            record("Book appointment: note / reason after target", f"/cases/{cid}", t, err, txt)
            st = q1("select start from appointments where case_id=? order by id desc", cid)
            RES.append({"field": "Book appointment: time (local Dubai input -> UTC stored)", "entered_local": when, "db_start_utc": st[0] if st else None,
                        "ok": bool(st and st[0][:16] == q1("select datetime(?, '-4 hours')", when.replace("T", " "))[0][:16])})
            co.action("attendance"); co.p.select_option("#aform select[name=attended]", "false"); co.p.select_option("#aform select[name=barrier]", index=2)
            bar = co.p.eval_on_selector("#aform select[name=barrier]", "s=>s.value"); err = co.submit_form()
            RES.append({"field": "Attendance: no-show barrier", "error": err, "chosen": bar, "db": q1("select barrier_code from appointments where case_id=? order by id desc", cid)})
            co.case(cid); co.action("book"); co.p.fill("#aform input[name=start]", when); co.submit_form()
        step("booking", booking)

        def care():
            sp.case(cid); sp.action("attendance"); sp.p.select_option("#aform select[name=attended]", "true"); sp.submit_form()
            sp.case(cid); sp.action("start_care"); t = tok("careplan")
            sp.p.select_option("#aform select[name=kind]", "surveillance"); sp.p.fill("#aform textarea[name=description]", t)
            sp.p.fill("#aform input[name=review_due_on]", "2029-06-14")
            err = sp.submit_form(); sp.case(cid); record("Care plan: description", f"/cases/{cid}", t, err, sp.case_text(), {"kind": q1("select kind from care_plans where description like ?", f"%{key(t)}%")})
            rd = q1("select review_due_on from care_plans where description like ?", f"%{key(t)}%")
            RES.append({"field": "Care plan: review / re-test due date", "page": f"/cases/{cid}", "db_row": rd,
                        "ok": bool(rd and str(rd[0]).startswith("2029-06-14") and "2029-06-14" in sp.case_text())})
            k = sp.p.eval_on_selector_all("#case-root aside [data-k]", "e=>e.map(x=>x.dataset.k)")
            sp.action("close_evidence" if "close_evidence" in k else k[0]); t = tok("close.note")
            sp.p.fill("#aform textarea[name=note]", t); err = sp.submit_form(); sp.case(cid); record("Close with evidence: note", f"/cases/{cid}", t, err, sp.case_text())
        step("care + close", care)

        lead = Browser(pw, "fatima.al.mansoori")

        def lead_flow():
            lead.case(cid); lead.action("reopen"); t = tok("reopen.note"); lead.p.fill("#aform textarea[name=note]", t)
            err = lead.submit_form(); lead.case(cid); record("Re-open: reason", f"/cases/{cid} (clinical lead)", t, err, lead.case_text())
            c2 = S.get("case1")
            lead.case(c2); lead.action("patient_deceased"); t = tok("death.note") + " registry notification"
            lead.p.fill("#aform textarea[name=note]", t); err = lead.submit_form(); lead.case(c2); record("Record death: source", f"/cases/{c2}", t, err, lead.case_text())
            lead.action("patient_deceased_revoke"); t = tok("death.fix") + " recorded against the wrong patient"
            lead.p.fill("#aform textarea[name=note]", t); err = lead.submit_form(); lead.case(c2); record("Correct death record: reason", f"/cases/{c2}", t, err, lead.case_text())
            # validation error keeps text
            lead.action("decline") if "decline" in lead.p.eval_on_selector_all("#case-root aside [data-k]", "e=>e.map(x=>x.dataset.k)") else None
            esc = q1("select e.id, e.case_id from escalations e join cases c on c.id=e.case_id join patients p on p.id=c.patient_id where e.acknowledged_at is null and p.managing_org_id=3 limit 1")
            if esc:
                lead.case(esc[1]); lead.p.click('.tabs button[data-t="events"]'); lead.p.locator(f'#tabbody button:has-text("Acknowledge")').first.click()
                t = tok("esc.ack"); lead.p.fill("dialog.dlg[open] textarea", t); lead.p.click("dialog.dlg[open] [data-ok]")
                lead.p.wait_for_selector("dialog.dlg[open]", state="detached", timeout=10000); lead.case(esc[1]); record("Acknowledge escalation: action note", f"/cases/{esc[1]}", t, None, lead.case_text())
        step("clinical lead flow", lead_flow)

        def validation():
            c3 = S.get("case2"); co.case(c3)
            co.action("consent"); short = "no"
            co.p.fill("#aform textarea[name=note]", short); co.p.evaluate("() => document.querySelector('#aform textarea[name=note]').removeAttribute('required')")
            err = co.submit_form(); kept = co.p.input_value("#aform textarea[name=note]") if co.p.locator("#aform textarea[name=note]").count() else None
            RES.append({"field": "Validation error keeps the typed text + shows message (decline note too short)", "page": f"/cases/{c3}", "error_shown": err, "text_kept": kept == short, "ok": bool(err) and kept == short})
            co.shot("validation_error_kept")
        step("validation", validation)

        adm = Browser(pw, "admin")

        def admin_flow():
            p = adm.p; p.goto(BASE + "/admin/users"); p.wait_for_selector("#users table")
            p.click("#add"); uname = f"e2e.{RUN}.{VIEW}"
            p.fill("#f-name", "Noor E2E عربي"); p.fill("#f-user", uname); p.select_option("#f-role", "analyst")
            site, team = tok("site"), tok("team"); p.fill("#f-site", site); p.fill("#f-team", team)
            p.click("#add-form button[type=submit]"); p.wait_for_selector("#created .card", timeout=10000)
            p.reload(); p.wait_for_selector("#users table"); p.select_option("#us", ""); txt = p.inner_text("#users")
            record("Add user: site", "/admin/users", site, None, txt); record("Add user: team", "/admin/users", team, None, txt)
            uid = q1("select id from users where username=?", uname)[0]
            p.locator(f'button[onclick="changeRole({uid})"]').click(); site2 = tok("site2")
            p.fill("dialog.dlg[open] #ds", site2); p.fill("dialog.dlg[open] #dt", ""); p.click("dialog.dlg[open] [data-ok]"); p.wait_for_selector("dialog.dlg[open]", state="detached")
            p.reload(); p.wait_for_selector("#users table"); p.select_option("#us", ""); txt = p.inner_text("#users")
            record("Change role: site", "/admin/users", site2, None, txt); RES.append({"field": "Change role: team cleared", "db": q1("select team from users where id=?", uid)})
            p.locator(f'button[onclick="deact({uid})"]').click(); t = tok("deact"); p.fill("dialog.dlg[open] textarea", t); p.click("dialog.dlg[open] [data-ok]")
            p.wait_for_selector("dialog.dlg[open]", state="detached"); p.reload(); p.wait_for_selector("#users table"); p.select_option("#us", ""); txt = p.inner_text("#users")
            record("Deactivate user: reason", "/admin/users", t, None, txt)
            p.locator(f'button[onclick="act({uid})"]').click()
            if p.locator("dialog.dlg[open] textarea").count():
                t = tok("react"); p.fill("dialog.dlg[open] textarea", t)
            else:
                t = None
            p.click("dialog.dlg[open] [data-ok]"); p.wait_for_selector("dialog.dlg[open]", state="detached"); p.reload(); p.wait_for_selector("#users table"); p.select_option("#us", "")
            record("Reactivate user: reason", "/admin/users", t, None if t else "no reason field", p.inner_text("#users"))
            adm.shot("admin_users_saved")
        step("admin flow", admin_flow)

        def gov():
            p = lead.p; p.goto(BASE + "/governance?tab=content"); p.wait_for_selector("#gov table", timeout=20000)
            b = p.locator("#gov button:has-text('Sign off')").first; b.click(); t = tok("signoff") + " wording checked"
            p.fill("dialog.dlg[open] textarea", t); p.click("dialog.dlg[open] [data-ok]"); p.wait_for_timeout(1500)
            still = p.locator("dialog.dlg[open]").count(); errtxt = p.inner_text("dialog.dlg[open] .form-error") if still and p.locator("dialog.dlg[open] .form-error").count() else None
            if still:
                p.click("dialog.dlg[open] [data-cancel]")
            p.reload(); p.wait_for_selector("#gov table", timeout=20000); record("Content sign-off: note", "/governance (content)", t, errtxt, p.inner_text("#gov"))
        step("governance", gov)

        def patient():
            for u, lang in (("patient.ar", "hi"), ("patient.en", "ur")):
                pt = Browser(pw, u); p = pt.p; p.goto(BASE + "/patient"); p.wait_for_selector(".lang-switch")
                p.click(f'.lang-switch button[data-l="{lang}"]'); p.wait_for_timeout(1200)
                p2 = pt.ctx.new_page(); p2.goto(BASE + "/patient"); p2.wait_for_selector(".lang-switch")
                RES.append({"field": f"Patient language choice kept on next visit ({u} -> {lang})", "page": "/patient",
                            "ok": p2.evaluate("() => document.documentElement.lang") == lang, "db": q1("select ui_language from users where username=?", u)})
                p2.close(); t = tok(f"pq{lang}")
                p.fill("#q", "hi"); p.click("#ask"); p.wait_for_timeout(500)
                short_msg = p.locator(".form-error").count() > 0
                p.fill("#q", t); p.click("#ask"); p.wait_for_timeout(1500); p.reload(); p.wait_for_selector(".lang-switch")
                record(f"Patient question to clinician ({u})", "/patient", t, None, p.inner_text("#p-root"), {"too_short_message": short_msg})
                pt.shot(f"patient_{u}")
                p.click('.lang-switch button[data-l="en"]' if u == "patient.en" else '.lang-switch button[data-l="ar"]'); p.wait_for_timeout(800)
                pt.ctx.close()
        step("patient", patient)

        def patient_tb():
            pt = Browser(pw, "patient.hi"); p = pt.p; p.goto(BASE + "/patient?lang=hi"); p.wait_for_selector(".lang-switch")
            if p.locator("#tbf").count():
                p.check('#tbf input[name="q1"][value="a"]'); p.check('#tbf input[name="q2"][value="b"]')
                p.click('.lang-switch button[data-l="en"]'); p.wait_for_timeout(1200)  # radio choices must survive a language switch
                kept = p.locator('#tbf input[name="q2"][value="b"]').is_checked()
                p.check('#tbf input[name="q3"][value="a"]'); p.check('#tbf input[name="conf"][value="5"]'); p.click("#tbf button[type=submit]"); p.wait_for_timeout(1800)
                p.reload(); p.wait_for_selector(".lang-switch"); shown = p.locator(".q-saved").count() > 0
                cid0 = q1("select c.id from cases c join patients pa on pa.id=c.patient_id join users u on u.patient_id=pa.id where u.username='patient.hi' order by c.id desc")
                r = q1("select confidence, answers from questionnaire_responses where case_id=? order by id desc", cid0[0]) if cid0 else None
                RES.append({"field": "Patient teach-back (answers, confidence, kept across language switch)", "page": "/patient", "db_row": r,
                            "ok": bool(kept and shown and r and r[0] == 5 and '"q2": "b"' in r[1])})
                pt.shot("patient_teachback_saved")
            pt.ctx.close()
            pt = Browser(pw, "patient.ur"); p = pt.p; p.goto(BASE + "/patient"); p.wait_for_selector(".lang-switch")
            if p.locator("#decline").count():
                p.locator("#p-root details summary").last.click(); t = tok("decline.patient"); p.fill("#dn", t); p.click("#decline")
                p.click("dialog.dlg[open] [data-ok]"); p.wait_for_timeout(1800)
                cid0 = q1("select c.id from cases c join patients pa on pa.id=c.patient_id join users u on u.patient_id=pa.id where u.username='patient.ur' order by c.id desc")[0]
                co.case(cid0); record("Patient decline: reason (seen by care team)", f"/patient -> /cases/{cid0}", t, None, co.case_text())
            pt.ctx.close()
        step("patient teach-back + decline", patient_tb)

        def translator():
            tr = Browser(pw, "translator"); p = tr.p; p.goto(BASE + "/governance?tab=content"); p.wait_for_selector("#gov table", timeout=20000)
            b = p.locator("#gov button:has-text('Sign off')")
            if b.count():
                b.first.click(); t = tok("trsign") + " translation checked"
                p.fill("dialog.dlg[open] textarea", t); p.click("dialog.dlg[open] [data-ok]"); p.wait_for_timeout(1500)
                still = p.locator("dialog.dlg[open]").count(); errtxt = p.inner_text("dialog.dlg[open] .form-error") if still and p.locator("dialog.dlg[open] .form-error").count() else None
                p.reload(); p.wait_for_selector("#gov table", timeout=20000); record("Translator sign-off: note", "/governance (content, translator)", t, errtxt, p.inner_text("#gov"))
            else:
                RES.append({"field": "Translator sign-off: note", "ok": False, "error": "no Sign off button for translator"})
            if tr.errs: RES.append({"field": "HTTP/JS errors seen as translator", "errors": tr.errs[-12:]})
            tr.ctx.close()
        step("translator", translator)

        def analyst():
            an = Browser(pw, "analyst"); p = an.p
            for path in ("/overview", "/dashboard", "/queue", "/audit"):
                p.goto(BASE + path); p.wait_for_timeout(2500)
            p.goto(BASE + f"/cases/{cid}"); p.wait_for_timeout(2500)
            n = p.locator("#case-root aside [data-k]").count()
            RES.append({"field": "Analyst is read-only (no data-changing actions on a case)", "ok": n == 0, "actions_offered": n})
            if an.errs: RES.append({"field": "HTTP/JS errors seen as analyst", "errors": an.errs[-12:], "ok": all(" 403 " in e or e.startswith("403") for e in an.errs)})
            an.ctx.close()
        step("analyst", analyst)

        def patients_flow():  # Patients: register (every field), edit a section, possible-duplicate override
            p = co.p; letters = "".join("bcdfghjklmnpqrstvwz"[int(c)] for c in RUN)
            given, family = f"Zé{letters} عربي", f"Qa{letters}{VIEW[:3]} عربي"
            body = f"784{1979}{random.randint(0, 9_999_999):07d}"
            t_ = 0
            for i, c in enumerate(reversed(body)):
                x = int(c) * (2 if i % 2 == 0 else 1); t_ += x - 9 if x > 9 else x
            eid = f"{body[:3]}-{body[3:7]}-{body[7:]}-{(10 - t_ % 10) % 10}"
            addr, city = tok("pt.address"), tok("pt.city")
            p.goto(BASE + "/patients/new"); p.wait_for_selector("#regf", timeout=20000)
            f = p.locator("#regf")
            f.locator("[name=given_name]").fill(given); f.locator("[name=family_name]").fill(family)
            f.locator("[name=birth_date]").fill("1979-09-21"); f.locator("[name=gender]").select_option("female")
            f.locator("[name=national_id]").fill(eid); f.locator("[name=nationality]").select_option("Jordan")
            f.locator("[name=language]").select_option("ur"); f.locator("[name=phone]").fill("052 123 4567")
            f.locator("[name=email]").fill(f"ui{RUN}{VIEW}@example.ae"); f.locator("[name=whatsapp]").check()
            f.locator("[name=preferred_channel]").select_option("whatsapp"); f.locator("[name=address_line]").fill(addr)
            f.locator("[name=city]").fill(city); f.locator("[name=emirate]").select_option("Abu Dhabi"); f.locator("[name=region]").select_option("Al Dhafra")
            f.locator("[name=managing_org_id]").select_option(index=1)
            f.locator("[name=consent_care_coordination]").select_option("permit")
            co.shot("patient_register")
            f.locator("button[type=submit]").click()
            p.wait_for_url(re.compile(r".*/patients/\d+.*"), timeout=15000); p.wait_for_selector("#pr-root .case-head", timeout=15000)
            pid = int(re.search(r"/patients/(\d+)", p.url).group(1)); S["new_patient"] = pid
            p.reload(); p.wait_for_selector("#pr-root .case-head"); txt = p.inner_text("#pr-root")
            row = q1("select birth_date, national_id, phone, email, region, preferred_channel, language, nationality from patients where id=?", pid)
            for fld, t in (("given name", given), ("family name", family), ("address", addr), ("city", city)):
                record(f"Register patient: {fld}", "/patients/new", t, None, txt)
            RES.append({"field": "Register patient: date of birth, Emirates ID, phone, email, region, channel, language, nationality",
                        "page": "/patients/new", "stored": row, "ok": row == ("1979-09-21", eid, "+971521234567", f"ui{RUN}{VIEW}@example.ae", "Al Dhafra", "whatsapp", "ur", "Jordan")
                        and eid in txt and "+971521234567" in txt})
            co.shot("patient_record")
            p.click('[data-edit="contact"]'); t = tok("pt.address2")
            p.fill('form[data-form="contact"] [name=address_line]', t); p.click('form[data-form="contact"] button[type=submit]')
            p.wait_for_function("() => !document.querySelector('form[data-form=contact]')", timeout=15000)
            p.reload(); p.wait_for_selector("#pr-root .case-head"); record("Edit patient contact: address", f"/patients/{pid}", t, None, p.inner_text("#pr-root"))
            # same name + date of birth again: possible duplicate -> reason -> create anyway
            p.goto(BASE + "/patients/new"); p.wait_for_selector("#regf"); f = p.locator("#regf")
            f.locator("[name=given_name]").fill(given); f.locator("[name=family_name]").fill(family); f.locator("[name=birth_date]").fill("1979-09-21")
            f.locator("[name=gender]").select_option("female"); f.locator("[name=nationality]").select_option("Jordan")
            f.locator("[name=region]").select_option("Abu Dhabi"); f.locator("[name=managing_org_id]").select_option(index=1)
            f.locator("button[type=submit]").click(); p.wait_for_selector("#anyway", timeout=15000)
            kept = f.locator("[name=given_name]").input_value() == given
            co.shot("patient_duplicate")
            t = tok("pt.dupreason"); p.fill("[name=create_anyway_reason]", t); p.click("#anyway")
            p.wait_for_url(re.compile(r".*/patients/\d+.*"), timeout=15000); p.wait_for_selector("#pr-root .case-head")
            p.reload(); p.wait_for_selector("#pr-root .case-head")
            record("Possible duplicate: create-anyway reason", "/patients/new", t, None, p.inner_text("#pr-root"), {"values_kept_on_warning": kept})
            S["dup_patient"] = int(re.search(r"/patients/(\d+)", p.url).group(1))
        step("patients", patients_flow)

        def invite_flow():  # Patient app account: code given in person -> patient activates on /activate (ar)
            pid = S["new_patient"]; p = co.p
            p.goto(f"{BASE}/patients/{pid}"); p.wait_for_selector("#invite-form", timeout=15000)
            p.check('#invite-form input[value="in_person"]'); p.click("#invite-form button[type=submit]")
            p.wait_for_selector("#new-code .code-big", timeout=15000); code = p.inner_text("#new-code .code-big").strip()
            co.shot("patient_account_code")
            uname = q1("select username from users where patient_id=?", pid)[0]
            ctx = pw.ctx_browser.new_context(**VIEWS[VIEW], locale="ar-AE", timezone_id="Asia/Dubai"); a = ctx.new_page()
            errs = []; a.on("pageerror", lambda e: errs.append("JS " + str(e)))
            a.goto(BASE + "/activate?lang=ar"); a.wait_for_selector("#act-form")
            a.fill("#code", "ZZZZ-ZZZZ"); a.fill("#birth_date", "1979-09-21"); a.fill("#password", "Lantern-Orbit-Cobalt-77"); a.fill("#confirm", "Lantern-Orbit-Cobalt-77")
            a.click("#act-form button[type=submit]"); a.wait_for_selector("#act-error")
            kept = a.input_value("#code") == "ZZZZ-ZZZZ" and a.input_value("#birth_date") == "1979-09-21"
            sw, iw = a.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
            a.screenshot(path=str(OUT / f"{VIEW}_activate_error_ar.png"), full_page=False)
            a.fill("#code", code); a.fill("#password", "Lantern-Orbit-Cobalt-77"); a.fill("#confirm", "Lantern-Orbit-Cobalt-77")
            a.click("#act-form button[type=submit]"); a.wait_for_load_state("networkidle")
            ok = "حسابك جاهز" in a.inner_text("body") and uname in a.inner_text("body")
            a.screenshot(path=str(OUT / f"{VIEW}_activate_done_ar.png"), full_page=False)
            a.click("a[href^='/patient']"); a.wait_for_selector(".lang-switch", timeout=15000); a.wait_for_timeout(500)
            a.screenshot(path=str(OUT / f"{VIEW}_patient_new_account_ar.png"), full_page=False)
            has_pw = q1("select password_hash is not null from users where patient_id=?", pid)[0] == 1
            RES.append({"field": "Patient account: code shown once, activation (ar) with wrong code kept values, then success", "page": "/activate",
                        "ok": ok and has_pw and kept and sw <= iw and not errs, "values_kept": kept, "overflow": sw > iw, "errors": errs})
            p.reload(); p.wait_for_selector("#sec-account"); RES.append({"field": "Patient record shows account Active", "ok": "Active" in p.inner_text("#sec-account")})
            ctx.close()
        step("invite", invite_flow)

        def recall_flow():  # Due for recall: send a simulated invitation, cancel another with a reason
            import datetime as _dt
            rows = sqlite3.connect(DB).execute("""select r.id, p.mrn, r.due_on from recalls r join patients p on p.id=r.patient_id
                where r.status in ('due','invited') and p.phone_valid=1 and p.deceased=0 order by r.due_on limit 2""").fetchall()
            if len(rows) < 2:
                RES.append({"field": "Recall list (needs 2 open recalls)", "ok": False, "found": len(rows)}); return
            p = co.p; out = []
            for k, (rid, mrn, due) in enumerate(rows):
                view = "due" if _dt.date.fromisoformat(str(due)[:10]) <= _dt.date.today() + _dt.timedelta(days=60) else "upcoming"
                p.goto(f"{BASE}/recalls?view={view}&q={mrn}"); p.wait_for_selector(f'[data-inv="{rid}"]', timeout=20000)
                if k == 0:
                    co.shot("recalls"); p.click(f'[data-inv="{rid}"]'); p.wait_for_timeout(1500)
                    r = q1("select status, invite_count, last_invite_channel from recalls where id=?", rid)
                    m = q1("select content from communications where category='recall_invite' and patient_id=(select patient_id from recalls where id=?) order by id desc", rid)
                    out.append({"field": "Recall: send invitation", "page": "/recalls", "db_row": r, "message": (m or [""])[0][:120],
                                "ok": bool(r and r[0] == "invited" and r[1] >= 1 and m and m[0].startswith("[SIMULATED"))})
                else:
                    p.click(f'[data-cancel="{rid}"]'); p.wait_for_selector("#dlg-text")
                    p.fill("#dlg-text", "no"); p.locator("[data-ok]").last.click(); p.wait_for_timeout(400)
                    still = p.locator("#dlg-text").count() == 1
                    t = tok("recall.cancel"); p.fill("#dlg-text", t); p.locator("[data-ok]").last.click(); p.wait_for_timeout(1500)
                    p.goto(f"{BASE}/patients/" + str(q1("select patient_id from recalls where id=?", rid)[0])); p.wait_for_selector("#pr-root .case-head")
                    co.shot("patient_record_recalls")
                    record("Recall: cancel reason", "/recalls", t, None, p.inner_text("#pr-root"), {"short_reason_refused": still})
            RES.extend(out)
        step("recall", recall_flow)

        def summary_flow():  # Care summaries: GP acknowledges the closure summary with a note
            row = sqlite3.connect(DB).execute("""select s.id, s.case_id from gp_summaries s join practitioners pr on pr.id=s.to_practitioner_id
                where s.acknowledged_at is null and (s.to_practitioner_id=19 or s.to_org_id=pr.org_id) and s.to_practitioner_id=19 order by s.id desc limit 1""").fetchone()
            if not row:
                RES.append({"field": "Care summary (needs one unacknowledged for Omar)", "ok": False}); return
            sid, cid_ = row; g = gp.p
            g.goto(BASE + "/summaries"); g.wait_for_selector(f'form.ack-form[data-id="{sid}"]', timeout=20000)
            gp.shot("summaries_inbox")
            t = tok("gp.ack"); g.fill(f'form.ack-form[data-id="{sid}"] textarea[name=note]', t); g.click(f'form.ack-form[data-id="{sid}"] button[type=submit]')
            g.wait_for_selector(f'form.ack-form[data-id="{sid}"]', state="detached", timeout=15000)
            gp.case(cid_); txt = gp.case_text()
            g.locator('.tabs button[data-t="events"]').click(); g.wait_for_timeout(200)
            g.evaluate("() => document.querySelectorAll('#tabbody details').forEach(d => d.open = true)")
            g.locator("#tabbody .sum-text").first.scroll_into_view_if_needed(); gp.shot("case_timeline_summary")
            record("Care summary: GP acknowledgement note", "/summaries", t, None, txt)
        step("summary", summary_flow)

        def coverage_flow():  # Insurance (expired -> warning), next of kin + guardian + proxy consent on the patient record
            pid = S["new_patient"]; p = co.p
            p.goto(f"{BASE}/patients/{pid}"); p.wait_for_selector("#sec-insurance", timeout=15000)
            p.click('[data-edit="insurance"]'); f = 'form[data-form="insurance"]'
            t = tok("ins.plan"); p.select_option(f"{f} [name=insurance_payer]", "daman_enhanced"); p.fill(f"{f} [name=insurance_plan]", t)
            p.fill(f"{f} [name=insurance_member_id]", "x"); p.fill(f"{f} [name=insurance_valid_to]", "2026-01-31")
            p.click(f"{f} button[type=submit]"); p.wait_for_timeout(500)
            kept = p.locator(f).count() == 1 and p.input_value(f"{f} [name=insurance_plan]") == t
            p.fill(f"{f} [name=insurance_member_id]", f"DMN-{RUN}"); p.click(f"{f} button[type=submit]")
            p.wait_for_function("() => !document.querySelector('form[data-form=insurance]')", timeout=15000)
            p.reload(); p.wait_for_selector("#sec-insurance"); txt = p.inner_text("#sec-insurance")
            row = q1("select insurance_payer, insurance_member_id, insurance_valid_to from patients where id=?", pid)
            record("Insurance: plan", f"/patients/{pid}", t, None, txt, {"values_kept_on_error": kept})
            RES.append({"field": "Insurance: payer, member number, end date; expired warning shown", "page": f"/patients/{pid}", "stored": row,
                        "ok": row == ("daman_enhanced", f"DMN-{RUN}", "2026-01-31") and p.locator('#sec-insurance [data-eligibility="expired"]').count() == 1 and kept})
            co.shot("patient_insurance")
            p.click('[data-edit="contacts"]'); f = 'form[data-form="contacts"]'
            p.fill(f"{f} [name=nok_name]", "Huda Rahman"); p.select_option(f"{f} [name=nok_relationship]", "parent")
            p.fill(f"{f} [name=nok_phone]", "055 765 4321"); p.check(f"{f} [name=nok_is_guardian]")
            p.select_option(f"{f} [name=proxy_consent]", "permit"); t2 = tok("proxy.note"); p.fill(f"{f} [name=proxy_note]", t2)
            p.click(f"{f} button[type=submit]"); p.wait_for_function("() => !document.querySelector('form[data-form=contacts]')", timeout=15000)
            p.reload(); p.wait_for_selector("#sec-contacts"); txt = p.inner_text("#sec-contacts")
            row = q1("select nok_name, nok_relationship, nok_phone, nok_is_guardian from patients where id=?", pid)
            dec = q1("select decision from consents where patient_id=? and scope='proxy_contact' and status='active'", pid)
            record("Next of kin: proxy consent note", f"/patients/{pid}", t2, None, txt)
            RES.append({"field": "Next of kin: name, relationship, phone, guardian, proxy consent", "page": f"/patients/{pid}", "stored": [row, dec],
                        "ok": row == ("Huda Rahman", "parent", "+971557654321", 1) and dec == ("permit",) and "Patient agrees" in txt})
            p.locator("#sec-contacts").scroll_into_view_if_needed(); co.shot("patient_next_of_kin")
        step("coverage", coverage_flow)

        for b in (co, gp, sp, lead, adm):
            if b.errs:
                RES.append({"field": f"HTTP/JS errors seen as {b.user}", "errors": b.errs[-12:]})
    (OUT / f"report_{VIEW}.json").write_text(json.dumps(RES, indent=1, ensure_ascii=False))
    bad = [r for r in RES if r.get("error") or r.get("audit_only") or r.get("shown_after_reload") is False or r.get("unicode_ok") is False or r.get("ok") is False or (("db" in r) and r.get("db") == [] and "shown_after_reload" in r)]
    print(json.dumps(RES, indent=1, ensure_ascii=False)[:20000]); print("PROBLEMS", len(bad))
    return len(bad)


if __name__ == "__main__":
    sys.exit(1 if run() else 0)
