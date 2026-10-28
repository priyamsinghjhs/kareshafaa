"""Screenshot of the patient page with locked teach-back answers (for Section 5.7).

Sending answers changes data, so run this ONLY against a scratch copy of the database (for example a second server on
port 8011 with DATABASE_URL pointing at a copy), never against the live service.
Usage: KS_CRED_FILE=... python lockshot.py http://localhost:8011
Output: build/shots/mobile_patient_locked_card.png (the password is never printed)."""
import os, sys
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright

B = sys.argv[1]
OUT = Path(__file__).parent / "build" / "shots"; OUT.mkdir(parents=True, exist_ok=True)
PW = [l.split("\t")[2] for l in Path(os.environ["KS_CRED_FILE"]).read_text().splitlines() if not l.startswith("#") and len(l.split("\t")) == 3][0]
with sync_playwright() as pw:
    b = pw.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    p = ctx.new_page(); p.goto(B + "/login"); p.fill("#username", "patient.en"); p.fill("#password", PW)
    p.click("button[type=submit]"); p.wait_for_load_state("networkidle")
    p.goto(B + "/patient", wait_until="networkidle"); p.wait_for_timeout(800)
    tb = ctx.request.get(B + "/api/me/case").json()
    if not tb["answers_lock"]["submitted"]:  # correct answers for the sample breast case (BI-RADS 0)
        p.evaluate("""async () => { await W.ready; return await W.api('/api/me/teach-back', {method:'POST',
                      body:{answers:{q1:'a',q2:'b',q3:'a'}, confidence:4, language:'en'}}); }""")
    p.goto(B + "/patient", wait_until="networkidle"); p.wait_for_timeout(1000)
    h = p.evaluate("document.documentElement.scrollHeight"); p.set_viewport_size({"width": 390, "height": h}); p.wait_for_timeout(300)
    p.screenshot(path=str(OUT / "mobile_patient_locked_full.png"))
    box = p.evaluate("""() => { const h = [...document.querySelectorAll('h2,h3,h4,b,strong')].find(e => /Your saved answers/.test(e.textContent));
        const card = h && h.closest('.card'); const r = card.getBoundingClientRect(); return [r.top + scrollY, r.bottom + scrollY]; }""")
    b.close()
im = Image.open(OUT / "mobile_patient_locked_full.png")
im.crop((0, max(0, int(box[0] * 2) - 20), im.width, int(box[1] * 2) + 20)).save(OUT / "mobile_patient_locked_card.png")
print("ok")
