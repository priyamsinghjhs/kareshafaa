"""Fresh screenshots of a running KareShafaa server for the operating document.

Usage: KS_CRED_FILE=/path/to/credentials.tsv python shots.py [BASE_URL]
The credentials file is tab-separated (username, role, password). The password is read in memory only and is
never printed or written anywhere. Output: build/shots/<device>_<name>.png
"""
import os, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
OUT = Path(__file__).parent / "build" / "shots"; OUT.mkdir(parents=True, exist_ok=True)
PW = None
for line in Path(os.environ["KS_CRED_FILE"]).read_text().splitlines():
    f = line.split("\t")
    if not line.startswith("#") and len(f) == 3:
        PW = f[2]; break

CASE = "{case}"
SHOTS = [  # (name, user, path, full_page_height or 0)
    ("login", None, "/login", 0),
    ("coord_queue", "aisha.rahman", "/queue", 0),
    ("coord_inbox", "aisha.rahman", "/ingest", 0),
    ("coord_case", "aisha.rahman", CASE, 1500),
    ("gp_review", "omar.haddad", "/queue?view=review", 0),
    ("gp_case", "omar.haddad", CASE, 1700),
    ("spec_queue", "vikram.mehta", "/queue", 0),
    ("spec_case", "vikram.mehta", CASE, 0),
    ("spec_consults", "vikram.mehta", "/consults", 0),
    ("lead_overview", "fatima.al.mansoori", "/overview", 0),
    ("lead_models", "fatima.al.mansoori", "/governance?tab=models", 0),
    ("lead_rules", "fatima.al.mansoori", "/governance?tab=rules", 0),
    ("lead_audit", "fatima.al.mansoori", "/audit", 0),
    ("analyst_dashboard", "analyst", "/dashboard", 1300),
    ("admin_users", "admin", "/admin/users", 0),
    ("admin_advanced", "admin", "/advanced", 1100),
    ("admin_audit", "admin", "/audit", 0),
    ("translator_content", "translator", "/governance?tab=content", 0),
    ("patient_en", "patient.en", "/patient", 1500),
    ("patient_ar", "patient.ar", "/patient", 1500),
    ("patient_hi", "patient.hi", "/patient", 1500),
]
ELEMENTS = [  # (name, user, selector list) on the same case as the *_case shot, desktop only
    ("coord_case_step", "aisha.rahman", ["#step-h", "section[aria-labelledby=act-h]"]),
    ("gp_case_step", "omar.haddad", ["#step-h", "section[aria-labelledby=act-h]"]),
]
ONLY = set(os.environ.get("KS_ONLY", "").split(",")) - {""}
DEVICES = {"desktop": (1280, 800, False, 1.5), "mobile": (390, 844, True, 2)}

with sync_playwright() as pw:
    b = pw.chromium.launch(channel="chrome", headless=True)
    for dn, (w, h, mob, sc) in DEVICES.items():
        sessions = {}
        for name, user, path, full in SHOTS:
            if ONLY and name not in ONLY:
                continue
            ctx = sessions.get(user)
            if ctx is None:
                ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=sc, is_mobile=mob, has_touch=mob)
                if user:
                    p = ctx.new_page(); p.goto(BASE + "/login", wait_until="networkidle")
                    p.fill("#username", user); p.fill("#password", PW); p.click("button[type=submit]")
                    p.wait_for_load_state("networkidle"); p.close()
                sessions[user] = ctx
            if path == CASE:
                items = ctx.request.get(BASE + "/api/queue?limit=20").json()["items"]
                pick = next((i for i in items if i.get("state") in ("acknowledged", "consultation", "confirmatory_test", "referral_sent")), items[0])
                path = f"/cases/{pick['id']}"
            p = ctx.new_page(); p.goto(BASE + path, wait_until="networkidle"); p.wait_for_timeout(900)
            if full:
                hh = min(full, p.evaluate("document.documentElement.scrollHeight"))
                p.set_viewport_size({"width": w, "height": max(h, hh)}); p.wait_for_timeout(300)
            p.screenshot(path=str(OUT / f"{dn}_{name}.png"))
            print(dn, name, path)
            p.close()
        if dn == "desktop":
            for name, user, sels in ELEMENTS:
                if ONLY and name not in ONLY:
                    continue
                ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=sc)
                p = ctx.new_page(); p.goto(BASE + "/login", wait_until="networkidle")
                p.fill("#username", user); p.fill("#password", PW); p.click("button[type=submit]"); p.wait_for_load_state("networkidle")
                items = ctx.request.get(BASE + "/api/queue?limit=20").json()["items"]
                pick = next((i for i in items if i.get("state") in ("acknowledged", "consultation", "confirmatory_test", "referral_sent")), items[0])
                p.goto(BASE + f"/cases/{pick['id']}", wait_until="networkidle"); p.wait_for_timeout(900)
                boxes = [p.locator(sel).first.bounding_box() for sel in sels]
                boxes = [bx for bx in boxes if bx]
                x0 = min(bx["x"] for bx in boxes) - 8; y0 = min(bx["y"] for bx in boxes) - 8
                x1 = max(bx["x"] + bx["width"] for bx in boxes) + 8; y1 = max(bx["y"] + bx["height"] for bx in boxes) + 8
                p.screenshot(path=str(OUT / f"{dn}_{name}.png"), full_page=True, clip={"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0})
                print(dn, name, pick["id"]); ctx.close()
        for c in sessions.values():
            c.close()
    b.close()
