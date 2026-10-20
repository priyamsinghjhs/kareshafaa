"""Optional headless-browser UI smoke test (dev only; needs `pip install playwright` + a local Chrome).

Logs in as each demo role, opens the role's pages, and fails on any JS error or failed API call.
Usage: python scripts/ui_smoke.py [base_url] [screenshot_dir]
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/wasl_shots")
OUT.mkdir(parents=True, exist_ok=True)
PLAN = {
    "aisha.rahman": ["/queue", "/cases/2001", "/cases/5", "/ingest", "/demo"],
    "omar.haddad": ["/queue", "/consults"],
    "vikram.mehta": ["/queue", "/consults"],
    "fatima.al.mansoori": ["/queue", "/dashboard", "/governance", "/audit", "/cases/2002"],
    "analyst": ["/dashboard", "/governance"],
    "admin": ["/governance", "/audit"],
    "patient.en": ["/patient"], "patient.ar": ["/patient"], "patient.hi": ["/patient"], "patient.ur": ["/patient"], "patient.tl": ["/patient"],
}


def main() -> int:
    problems: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        for user, pages in PLAN.items():
            ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
            r = ctx.request.post(f"{BASE}/api/auth/demo-login", data={"username": user})
            assert r.ok, (user, r.status)
            page = ctx.new_page()
            page.on("pageerror", lambda e, u=user: problems.append(f"{u}: JS error {e}"))
            page.on("console", lambda m, u=user: problems.append(f"{u}: console {m.text}") if m.type == "error" else None)
            page.on("response", lambda resp, u=user: problems.append(f"{u}: {resp.status} {resp.url}")
                    if "/api/" in resp.url and resp.status >= 400 else None)
            for p in pages:
                page.goto(BASE + p, wait_until="networkidle")
                page.wait_for_timeout(400)
                page.screenshot(path=str(OUT / f"{user}{p.replace('/', '_')}.png"), full_page=False)
            ctx.close()
        # interactive flows: HL7 ingest, one-click scenario, patient teach-back in Arabic (RTL)
        ctx = browser.new_context()
        ctx.request.post(f"{BASE}/api/auth/demo-login", data={"username": "aisha.rahman"})
        page = ctx.new_page()
        page.on("pageerror", lambda e: problems.append(f"flow: JS error {e}"))
        page.goto(BASE + "/ingest", wait_until="networkidle")
        page.get_by_text("Load sample (positive FIT)").click()
        page.wait_for_timeout(300)
        page.get_by_text("Ingest message", exact=True).click()
        page.wait_for_selector("#ack:has-text('MSA|')", timeout=5000)
        if "case #" not in page.inner_text("#ack"):
            problems.append("flow: HL7 sample did not create a case: " + page.inner_text("#ack"))
        page.goto(BASE + "/demo", wait_until="networkidle")
        page.locator("button", has_text="Start scenario").first.click()
        page.wait_for_selector("text=Ready: case", timeout=10000)
        page.screenshot(path=str(OUT / "flow_demo.png"))
        ctx.close()
        ctx = browser.new_context()
        ctx.request.post(f"{BASE}/api/auth/demo-login", data={"username": "patient.ar"})
        page = ctx.new_page()
        page.goto(BASE + "/patient", wait_until="networkidle")
        if page.evaluate("document.querySelector('[dir=rtl]') === null"):
            problems.append("flow: Arabic patient page is not RTL")
        page.screenshot(path=str(OUT / "flow_patient_ar.png"))
        ctx.close()
        browser.close()
    for p in problems:
        print("PROBLEM", p)
    print(f"checked {sum(len(v) for v in PLAN.values())} pages, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
