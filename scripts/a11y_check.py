"""Accessibility check (WCAG 2.1 A + AA rules of axe-core) and a keyboard focus check, through the real login form,
in English and the patient app's Arabic/Hindi/Urdu. Dev and CI only; never run against the hosted service.

    npm install --no-save axe-core@4        # once (or set KARESHAFAA_AXE_JS to axe.min.js)
    KARESHAFAA_DEMO_CREDENTIALS_FILE=... python scripts/a11y_check.py http://127.0.0.1:8042 [out.json]
Exit code 1 if any page has a WCAG violation or a focusable element without a visible focus indicator.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else None
AXE = Path(os.environ.get("KARESHAFAA_AXE_JS") or Path(__file__).resolve().parents[1] / "node_modules/axe-core/axe.min.js")
TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]
PLAN = {  # user (None = signed out): [(name, path)]
    None: [("login", "/login"), ("forgot password", "/forgot-password"), ("offline", "/offline"), ("starting", "/starting?preview=1")],
    "aisha.rahman": [("queue", "/queue"), ("case", "/cases/{case}"), ("patients", "/patients?q=SYN-"),
                     ("patient record", "/patients/{patient}"), ("register patient", "/patients/new"),
                     ("results inbox", "/ingest"), ("recalls", "/recalls?view=upcoming")],
    "omar.haddad": [("gp review", "/queue?view=review"), ("summaries", "/summaries?view=all"), ("consults", "/consults")],
    "fatima.al.mansoori": [("overview", "/overview"), ("outcomes", "/dashboard"), ("governance decisions", "/governance?tab=decisions"),
                           ("governance overrides", "/governance?tab=overrides"), ("governance privacy", "/governance?tab=privacy"),
                           ("governance models", "/governance?tab=models"), ("audit", "/audit")],
    "admin": [("admin users", "/admin/users"), ("admin system", "/admin/system"), ("admin about", "/admin/about")],
    "patient.en": [("patient app en", "/patient"), ("patient app hi", "/patient?lang=hi"), ("patient app ur", "/patient?lang=ur")],
    "patient.ar": [("patient app ar", "/patient")],
}


def password_for(user: str) -> str:
    for line in Path(os.environ["KARESHAFAA_DEMO_CREDENTIALS_FILE"]).read_text().splitlines():
        if line and not line.startswith("#"):
            u, _r, p = line.split("\t")
            if u == user:
                return p
    return os.environ["KARESHAFAA_DEMO_PASSWORD"]


def main() -> int:
    axe = AXE.read_text()
    report, problems = {}, []
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel=os.environ.get("KARESHAFAA_BROWSER_CHANNEL", "chrome") or None, headless=True)
        for user, pages in PLAN.items():
            ctx = b.new_context(viewport={"width": 1280, "height": 900})
            ctx.add_init_script("for (const r of ['coordinator','gp','specialist','clinical_lead','analyst','admin','translator']) localStorage.setItem('ks-guide-'+r+'-v1','1');")
            p = ctx.new_page()
            if user:
                p.goto(BASE + "/login", wait_until="networkidle")
                p.fill("#username", user)
                p.fill("#password", password_for(user))
                p.click("button[type=submit]")
                p.wait_for_load_state("networkidle")
            for name, path in pages:
                if "{case}" in path:
                    path = path.replace("{case}", str(ctx.request.get(BASE + "/api/queue?view=all&limit=1").json()["items"][0]["id"]))
                if "{patient}" in path:
                    path = path.replace("{patient}", str(ctx.request.get(BASE + "/api/patients?q=SYN-").json()["items"][0]["id"]))
                p.goto(BASE + path, wait_until="networkidle")
                p.wait_for_timeout(700)
                p.add_script_tag(content=axe)
                r = p.evaluate("tags => axe.run(document, { runOnly: { type: 'tag', values: tags }, resultTypes: ['violations'] })", TAGS)
                v = [{"id": x["id"], "impact": x["impact"], "help": x["help"], "nodes": len(x["nodes"]),
                      "sample": x["nodes"][0]["target"] if x["nodes"] else None} for x in r["violations"]]
                p.evaluate("document.activeElement && document.activeElement.blur()")
                focus_bad = []
                for _ in range(25):
                    p.keyboard.press("Tab")
                    p.wait_for_timeout(120)  # let CSS transitions on the focus ring finish
                    bad = p.evaluate("""() => { const el = document.activeElement; if (!el || el === document.body) return null;
                       const cs = getComputedStyle(el);
                       const ring = (cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0) || (cs.boxShadow && cs.boxShadow !== 'none');
                       return ring ? null : el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + ' "' + (el.textContent || el.name || '').trim().slice(0, 30) + '"'; }""")
                    if bad and bad not in focus_bad:
                        focus_bad.append(bad)
                lang, dirv = p.evaluate("[document.documentElement.lang, (document.querySelector('[dir]') || document.documentElement).dir]")
                report[name] = {"path": path, "lang": lang, "dir": dirv, "violations": v, "focus_without_indicator": focus_bad}
                for x in v:
                    problems.append(f"{name}: {x['id']} ({x['impact']}, {x['nodes']} nodes) {x['help']} e.g. {x['sample']}")
                for f in focus_bad:
                    problems.append(f"{name}: no visible focus on {f}")
                print(f"{name:24s} lang={lang:3s} violations={len(v)} focus_issues={len(focus_bad)}")
            ctx.close()
        b.close()
    if OUT:
        OUT.write_text(json.dumps({"tags": TAGS, "pages": report, "problems": problems}, indent=1))
    print(f"{len(report)} pages checked; {len(problems)} problems")
    for x in problems:
        print(" -", x)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
