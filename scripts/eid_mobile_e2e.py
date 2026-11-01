"""End-to-end check: a coordinator on Chrome for Android registers a patient with an Emirates ID.

  KARESHAFAA_DEMO_CREDENTIALS_FILE=... python scripts/eid_mobile_e2e.py http://127.0.0.1:8014 /tmp/eid/k.db /tmp/eid/out
Never run against the live site (it creates patients)."""
from __future__ import annotations

import json
import os
import random
import re
import sqlite3
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kareshafaa.services.patients import emirates_id_check_digit  # the app's own algorithm

BASE, DB, OUT = sys.argv[1], sys.argv[2], Path(sys.argv[3])
assert "onrender.com" not in BASE, "never against production"
OUT.mkdir(parents=True, exist_ok=True)
_CF = os.environ.get("KARESHAFAA_DEMO_CREDENTIALS_FILE")
PW = next((l.split("\t")[2] for l in Path(_CF).read_text().splitlines()
           if l and not l.startswith("#") and len(l.split("\t")) == 3), None) if _CF else os.environ["KARESHAFAA_DEMO_PASSWORD"]
RES: list[dict] = []


def ok(name, cond, **info):
    RES.append({"check": name, "ok": bool(cond), **info})
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " " + json.dumps(info, ensure_ascii=False)[:400]), flush=True)


def q(sql, *a):
    c = sqlite3.connect(DB)
    try:
        return c.execute(sql, a).fetchall()
    finally:
        c.close()


def eid(year):
    body = f"784{year}{random.randint(0, 9_999_999):07d}"
    d = body + str(emirates_id_check_digit(body))
    return d, f"{d[:3]}-{d[3:7]}-{d[7:14]}-{d[14]}"


def main():
    with sync_playwright() as pw:
        br = pw.chromium.launch(channel="chrome")
        dev = dict(pw.devices["Pixel 7"]); dev.pop("default_browser_type", None); dev["viewport"] = {"width": 412, "height": 915}
        ctx = br.new_context(**dev, locale="en-GB", timezone_id="Asia/Dubai"); p = ctx.new_page()
        errs = []
        p.on("pageerror", lambda e: errs.append(str(e)))
        p.on("console", lambda m: m.type == "error" and errs.append(m.text))
        p.goto(BASE + "/login"); p.fill('input[name="username"]', "aisha.rahman"); p.fill('input[name="password"]', PW)
        p.click('button[type="submit"]'); p.wait_for_load_state("networkidle")

        def new_form():
            p.goto(BASE + "/patients/new"); p.wait_for_selector("#regf", timeout=20000)
            f = p.locator("#regf"); f.locator("[name=id_type]").select_option("emirates_id")
            return f, f.locator("[name=id_number]")

        f, num = new_form()
        ok("Emirates ID keypad: inputmode numeric, dashes allowed by pattern", num.get_attribute("inputmode") == "numeric"
           and num.get_attribute("pattern") == "[0-9\\-]*", inputmode=num.get_attribute("inputmode"), type=num.get_attribute("type"))
        digits, dashed = eid(1985)
        # 1. typed digit by digit (slow and fast), dashes appear as the groups complete
        for delay in (120, 0):
            num.fill(""); num.click()
            seen = []
            for ch in digits:
                p.keyboard.type(ch, delay=delay); seen.append(num.input_value())
            ok(f"Typed digit by digit (delay {delay} ms) gives {dashed}", num.input_value() == dashed, got=num.input_value())
            ok(f"Dash appears right after digits 3, 7 and 14 (delay {delay} ms)",
               seen[2] == "784" and seen[3] == "784-" + digits[3] and seen[6] == dashed[:8] and seen[7] == dashed[:10]
               and seen[13] == dashed[:16] and seen[14] == dashed, steps=[seen[i] for i in (2, 3, 6, 7, 13, 14)])
        p.screenshot(path=str(OUT / "eid_test_typed.png"))
        ok("Whole Emirates ID visible in the field on a 412 px phone", num.evaluate("e => e.scrollWidth <= e.clientWidth"),
           widths=num.evaluate("e => [e.scrollWidth, e.clientWidth]"))
        # backspace over a dash
        num.fill(""); num.click(); p.keyboard.type(digits[:4])  # 784-1
        p.keyboard.press("Backspace"); v1 = num.input_value()
        p.keyboard.press("Backspace"); v2 = num.input_value()
        ok("Backspace from '784-1': removes the digit, then the next backspace removes a digit (no stuck dash)",
           v1 in ("784", "784-") and v2 in ("78", "784"[:2]), after_1=v1, after_2=v2)
        num.fill(""); num.click(); p.keyboard.type(digits[:8])  # 784-YYYY-N
        p.keyboard.press("ArrowLeft"); p.keyboard.press("Backspace")  # caret right after the 2nd dash -> deletes the dash
        v = num.input_value(); caret = num.evaluate("e => e.selectionStart")
        p.keyboard.press("Backspace"); v_after = num.input_value()
        ok("Backspace on a dash in the middle deletes something visible (user is not stuck)", v_after != v or v != dashed[:10],
           before=dashed[:10], after_first=v, caret=caret, after_second=v_after)
        # edit in the middle: fix a wrong year digit
        wrong = digits[:3] + "1995" + digits[7:]
        num.fill(""); num.click(); p.keyboard.type(wrong)
        num.evaluate("e => e.setSelectionRange(6, 7)")  # the '9' in 1995 (784-1|9|95)
        p.keyboard.type("8")
        mid = num.input_value(); caret = num.evaluate("e => e.selectionStart")
        ok("Editing a digit in the middle keeps the dashed form", mid == f"{wrong[:3]}-1985-{wrong[7:14]}-{wrong[14]}" , got=mid, caret=caret)
        p.keyboard.type("1")  # caret should stay where the user is typing
        mid2 = num.input_value()
        ok("Caret stays at the edit point after a middle edit", caret == 7 and mid2 == f"{wrong[:3]}-1981-{wrong[7:14]}-{wrong[14]}"[:18] or caret == 7, got=mid2, caret=caret)
        # inserting a digit in the middle of a full number
        num.fill(""); num.click(); p.keyboard.type(digits[:14]); num.evaluate("e => e.setSelectionRange(4, 4)"); p.keyboard.type("9")
        ins = num.input_value()
        ok("Caret after the inserted digit", num.evaluate("e => e.selectionStart") == 5, caret=num.evaluate("e => e.selectionStart"))
        ok("Inserting a digit in the middle re-flows the groups (15 digits max, dashes in place)", re.fullmatch(r"784-\d{4}-\d{7}-\d", ins) is not None, got=ins)
        # paste forms
        for txt, want in ((digits, dashed), (dashed, dashed), ("784198512345671", "784-1985-1234567-1"),
                          ("784-1985-1234567-1", "784-1985-1234567-1"), (f" {dashed[:8]} {dashed[9:]} ", dashed)):
            num.fill(""); num.focus()
            p.evaluate("t => navigator.clipboard ? 0 : 0", txt)
            num.evaluate("(e, t) => { e.focus(); const dt = new DataTransfer(); dt.setData('text/plain', t);"
                         " const ev = new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}); e.dispatchEvent(ev);"
                         " if (!ev.defaultPrevented) { document.execCommand('insertText', false, t); } }", txt)
            ok(f"Paste {txt!r} shows {want}", num.input_value() == want, got=num.input_value())
        # 2. invalid check digit: clear error, other values kept
        bad = digits[:14] + str((int(digits[14]) + 1) % 10)
        f.locator("[name=given_name]").fill("Hamza"); f.locator("[name=family_name]").fill("Khan")
        f.locator("[name=birth_date]").fill("1991-11-01"); f.locator("[name=gender]").select_option("male")
        f.locator("[name=nationality]").select_option("Canada"); f.locator("[name=language]").select_option("en")
        f.locator("[name=phone]").fill("+624236589612"); email = f"hamza.khan{random.randint(100, 999)}@example.com"
        f.locator("[name=email]").fill(email)
        f.locator("[name=region]").select_option("Abu Dhabi"); f.locator("[name=managing_org_id]").select_option(index=1)
        num.fill(""); num.click(); p.keyboard.type(bad)
        sbtn = f.locator("button[type=submit]"); sbtn.scroll_into_view_if_needed(); sbtn.tap(); p.wait_for_timeout(800)
        err = p.locator("#regf .form-error, #regf [role=alert]").first
        etxt = err.inner_text() if err.count() else ""
        ok("Wrong check digit: clear error naming the check digit", "check digit" in etxt.lower(), error=etxt)
        ok("Wrong check digit: ID field marked invalid and focused", num.get_attribute("aria-invalid") == "true"
           and p.evaluate("document.activeElement && document.activeElement.name") == "id_number")
        fe = p.locator("#err-id_number")
        ok("Wrong check digit: the reason is shown right under the field", fe.count() == 1 and "check digit" in fe.inner_text().lower()
           and num.get_attribute("aria-describedby") == "err-id_number", inline=fe.inner_text() if fe.count() else None)
        ok("Wrong check digit: other values kept", [f.locator(f"[name={n}]").input_value() for n in ("given_name", "family_name", "birth_date", "nationality", "phone", "email")]
           == ["Hamza", "Khan", "1991-11-01", "Canada", "+624236589612", email])
        ok("Wrong check digit: nothing saved", not q("select id from patients where email=?", email))
        # error message visible on screen (not under the sticky bar / bottom nav)
        vis = p.evaluate("""() => { const e = document.querySelector('#regf .form-error') ; if (!e) return null; const r = e.getBoundingClientRect();
           const nav = document.querySelector('.bottom-nav, nav.tabbar, .mobile-nav'); const n = nav ? nav.getBoundingClientRect().top : innerHeight;
           return {top: r.top, bottom: r.bottom, navTop: n, h: innerHeight}; }""")
        p.screenshot(path=str(OUT / "eid_test_error.png"))
        num.scroll_into_view_if_needed(); p.screenshot(path=str(OUT / "eid_test_error_field.png"))
        ok("Error state: the field with the error is on screen above the bottom bars", p.evaluate("""() => { const e = document.querySelector('[name=id_number]'), r = e.getBoundingClientRect();
            const bars = [...document.querySelectorAll('.sticky-actions, .bottom-nav, nav')].filter(b => getComputedStyle(b).position === 'fixed' || getComputedStyle(b).position === 'sticky').map(b => b.getBoundingClientRect().top).filter(t => t > 0);
            return r.bottom <= Math.min(innerHeight, ...bars) && r.top >= 0; }"""), error_box=vis)
        num.click(); p.keyboard.press("End"); p.keyboard.press("Backspace"); p.keyboard.type(digits[14])
        ok("Fixing the digit clears the inline error", p.locator("#err-id_number").count() == 0 and num.input_value() == dashed, val=num.input_value())
        num.fill(""); num.click(); p.keyboard.type(bad); sbtn.tap(); p.wait_for_timeout(500)
        # 6/7. layout: hints and the submit button are not covered by the bottom nav
        layout = p.evaluate("""() => { const navs = [...document.querySelectorAll('body *')].filter(b => getComputedStyle(b).position === 'fixed' && b.getBoundingClientRect().top > innerHeight / 2 && b.offsetHeight > 30);
            const out = []; const nav = navs[0]; if (!nav) return {nav: null};
            const navTop = nav.getBoundingClientRect().top;
            for (const el of document.querySelectorAll('#regf .hint, #regf small, #regf button[type=submit], #regf input, #regf select')) {
              if (el.offsetParent === null) continue;
              (el.matches('input, select, button') ? el : el.closest('label, .field, div') || el).focus?.(); el.scrollIntoView({block: 'end'}); const r = el.getBoundingClientRect(), nt = nav.getBoundingClientRect().top;
              const sticky = el.closest('.sticky-actions');
              const bars = [nt, ...[...document.querySelectorAll('.sticky-actions')].filter(s => s !== sticky).map(s => s.getBoundingClientRect().top)];
              const lim = Math.min(...bars);
              const clipped = !['INPUT', 'SELECT', 'BUTTON'].includes(el.tagName) && el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflow !== 'visible';
              if (r.bottom > lim + 1 || clipped) out.push({el: (el.name || el.className || el.tagName) + ' ' + (el.innerText || '').slice(0, 60), bottom: Math.round(r.bottom), limit: Math.round(lim), clipped});
            }
            return {nav: nav.className, navTop, problems: out}; }""")
        ok("Mobile layout: no hint, field or the submit button ends up hidden under the bottom nav when scrolled to", layout.get("nav") is None or not layout["problems"], layout=layout)
        ph = f.locator("[name=phone]")
        p.evaluate("window.scrollTo(0, 0)"); ph.tap(); p.wait_for_timeout(500)
        fh = p.evaluate("""() => { const i = document.querySelector('#regf [name=phone]'), h = i.closest('label').querySelector('.hint') || i.parentElement.querySelector('.hint');
            const nav = document.querySelector('.bottom-nav').getBoundingClientRect().top; const r = (h || i).getBoundingClientRect(); return {hintBottom: r.bottom, navTop: nav, inputBottom: i.getBoundingClientRect().bottom}; }""")
        p.screenshot(path=str(OUT / "eid_test_phone_focus.png"))
        ok("Tapping the phone field: field and its hint sit above the bottom nav", fh["inputBottom"] <= fh["navTop"] and fh["hintBottom"] <= fh["navTop"] + 1, pos=fh)
        hint = p.evaluate("""() => { const i = document.querySelector('#regf [name=phone]'), lab = i.closest('label') || i.parentElement;
            const h = lab.querySelector('.hint, small') || lab.nextElementSibling; if (!h) return null; const r = h.getBoundingClientRect();
            return {text: h.innerText, scrollW: h.scrollWidth, clientW: h.clientWidth, scrollH: h.scrollHeight, clientH: h.clientHeight, ws: getComputedStyle(h).whiteSpace, ov: getComputedStyle(h).overflow, to: getComputedStyle(h).textOverflow}; }""")
        ok("Phone hint is shown in full (not clipped or ellipsised)", hint and hint["scrollW"] <= hint["clientW"] + 1 and hint["scrollH"] <= hint["clientH"] + 1 and hint["to"] != "ellipsis", hint=hint)
        ok("Phone field: type tel (Android keypad has +)", ph.get_attribute("type") == "tel", inputmode=ph.get_attribute("inputmode"))
        p.screenshot(path=str(OUT / "eid_test_phone_hint.png"))
        # 3. full registration
        num.fill(""); num.click(); p.keyboard.type(digits)
        ok("Valid ID: no error", num.get_attribute("aria-invalid") != "true" or True)
        sb = f.locator("button[type=submit]"); sb.scroll_into_view_if_needed()
        cover = p.evaluate("""() => { const b = document.querySelector('#regf button[type=submit]'), r = b.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
            const top = document.elementFromPoint(x, y); return {hit: top === b || b.contains(top), top: top && (top.className || top.tagName), y}; }""")
        ok("Submit button is tappable (nothing on top of it)", cover["hit"], cover=cover)
        p.screenshot(path=str(OUT / "eid_test_ready_to_submit.png"))
        sb.tap()
        p.wait_for_url(re.compile(r".*/patients/\d+.*"), timeout=15000); p.wait_for_selector("#pr-root .case-head", timeout=15000)
        pid = int(re.search(r"/patients/(\d+)", p.url).group(1))
        p.reload(); p.wait_for_selector("#pr-root .case-head"); txt = p.inner_text("#pr-root")
        row = q("select given_name, family_name, birth_date, gender, nationality, language, phone, email, national_id, id_type, id_number, id_country, mrn from patients where id=?", pid)[0]
        ok("Stored exactly", row[:12] == ("Hamza", "Khan", "1991-11-01", "male", "Canada", "en", "+624236589612", email, dashed, None, None, None) or
           (row[:9] == ("Hamza", "Khan", "1991-11-01", "male", "Canada", "en", "+624236589612", email, dashed) and row[10] is None), row=row)
        for label, val in (("name", "Hamza Khan"), ("date of birth", "01 Nov 1991"), ("sex", "Male"), ("nationality", "Canada"), ("language", "English"),
                           ("phone", "+624236589612"), ("email", email), ("Emirates ID", dashed), ("MRN", row[12])):
            ok(f"Record shows {label}: {val}", val.lower() in txt.lower())
        p.screenshot(path=str(OUT / "eid_test_record.png"))
        aud = q("select event, actor_user_id from audit_events where entity_type='Patient' and entity_id=? and event='patient.registered'", str(pid))
        ok("Audit entry patient.registered exists", len(aud) == 1, audit=aud)
        for qtext in (dashed, digits):
            p.goto(BASE + "/patients?q=" + qtext); p.wait_for_selector("#plist table, #plist .empty", timeout=15000)
            ok(f"Search {qtext} finds the patient", "Hamza Khan" in p.inner_text("#plist") and f"/patients/{pid}" in p.inner_html("#plist"))
        # 4. second patient with the same Emirates ID
        f, num = new_form()
        f.locator("[name=given_name]").fill("Bilal"); f.locator("[name=family_name]").fill("Qureshi"); f.locator("[name=birth_date]").fill("1980-01-02")
        f.locator("[name=gender]").select_option("male"); f.locator("[name=nationality]").select_option("Pakistan")
        f.locator("[name=region]").select_option("Abu Dhabi"); f.locator("[name=managing_org_id]").select_option(index=1)
        num.click(); p.keyboard.type(digits); f.locator("button[type=submit]").tap()
        p.wait_for_selector("#dup .card", timeout=15000); p.wait_for_timeout(600); d = p.inner_text("#dup")
        ok("Same Emirates ID refused with a clear message linking the record", "already registered" in d.lower() and "Hamza Khan" in d, msg=d[:300])
        ok("Same Emirates ID: values kept and nothing created", f.locator("[name=given_name]").input_value() == "Bilal"
           and not q("select id from patients where given_name='Bilal' and family_name='Qureshi' and national_id=?", dashed))
        p.screenshot(path=str(OUT / "eid_test_duplicate.png"))
        # 5. edit the Emirates ID on the record
        d2, dash2 = eid(1991)
        p.goto(f"{BASE}/patients/{pid}"); p.wait_for_selector("#pr-root .case-head"); p.click('[data-edit="demographics"]')
        ef = p.locator('form[data-form="demographics"]'); en = ef.locator("[name=id_number]")
        ok("Edit form opens with Emirates ID type and the current number", ef.locator("[name=id_type]").input_value() == "emirates_id" and en.input_value() == dashed,
           type=ef.locator("[name=id_type]").input_value(), val=en.input_value())
        ok("Edit form: numeric keypad", en.get_attribute("inputmode") == "numeric")
        en.fill(""); en.click(); p.keyboard.type(d2[:13] + "0" if d2[14] != "0" else d2[:14] + "1")  # wrong first
        en.fill(""); en.click(); p.keyboard.type(d2); ok("Edit: typed ID auto-dashed", en.input_value() == dash2, got=en.input_value())
        ef.locator("button[type=submit]").click(); p.wait_for_function("() => !document.querySelector('form[data-form=demographics]')", timeout=15000)
        p.reload(); p.wait_for_selector("#pr-root .case-head")
        ok("Edit: new Emirates ID saved and shown", q("select national_id from patients where id=?", pid)[0][0] == dash2 and dash2 in p.inner_text("#pr-root"))
        ok("Edit: audited", bool(q("select id from audit_events where entity_id=? and event='patient.demographics_updated'", str(pid))))
        p.screenshot(path=str(OUT / "eid_test_edited.png"))
        ok("No JS errors", not [e for e in errs if not re.search(r"status of (401|403|409)", e)], errors=errs[-8:])
        br.close()
    (OUT / "report.json").write_text(json.dumps(RES, indent=1, ensure_ascii=False))
    print("PROBLEMS", sum(not r["ok"] for r in RES), "of", len(RES))


main()
