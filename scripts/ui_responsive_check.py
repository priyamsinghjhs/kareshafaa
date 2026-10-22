"""Responsive UI check + screenshots (dev only; needs `pip install playwright` and a local Chrome).

Signs in through the real login form as each role, opens that role's main pages on several emulated devices and
checks: no horizontal overflow (document.scrollWidth <= innerWidth), interactive targets >= 44px on touch devices,
no JS errors, no failed API calls. Screenshots go to OUT_DIR.

Usage: KARESHAFAA_DEMO_PASSWORD=... python scripts/ui_responsive_check.py [base_url] [out_dir] [--devices all|key]
Passwords are read from the environment (or a credentials file via KARESHAFAA_DEMO_CREDENTIALS_FILE), never argv.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "http://localhost:8000"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else "/tmp/kareshafaa_ui")
MODE = "all" if "--devices" in sys.argv and sys.argv[sys.argv.index("--devices") + 1] == "all" else "key"
OUT.mkdir(parents=True, exist_ok=True)

DEVICES = {  # name: (width, height, mobile/touch, scale)
    "iphone-se": (375, 667, True, 2), "iphone-15-pro": (393, 852, True, 3), "pixel-7": (412, 915, True, 2.6),
    "galaxy-s-small": (360, 800, True, 3), "ipad": (820, 1180, True, 2), "laptop": (1366, 768, False, 1),
    "desktop": (1920, 1080, False, 1), "desktop-1280": (1280, 800, False, 1), "mobile-390": (390, 844, True, 2),
}
LANDSCAPE = {"iphone-se", "iphone-15-pro", "pixel-7", "galaxy-s-small"}
PLAN = {  # user: [(name, path)]
    "aisha.rahman": [("coordinator_home", "/queue"), ("case", "/cases/{case}"), ("ingest", "/ingest"), ("demo", "/demo")],
    "omar.haddad": [("gp_home", "/queue?view=review"), ("gp_consults", "/consults")],
    "vikram.mehta": [("specialist_home", "/queue?view=review")],
    "fatima.al.mansoori": [("lead_home", "/overview"), ("lead_governance", "/governance"), ("lead_dashboard", "/dashboard"),
                           ("lead_advanced", "/advanced"), ("lead_audit", "/audit")],
    "analyst": [("analyst_home", "/dashboard")],
    "admin": [("admin_users", "/admin/users")],
    "translator": [("translator_home", "/governance?tab=content")],
    "patient.ar": [("patient_ar", "/patient")], "patient.en": [("patient_en", "/patient")],
}
TAP_JS = """() => {
  const bad = [];
  const sel = 'button, [role=button], input:not([type=hidden]):not([type=radio]):not([type=checkbox]), select, textarea, summary, .bottom-nav a, .nav-list a, td a, .stat, .back-link';
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    if (!r.width || !r.height || cs.visibility === 'hidden' || el.closest('[hidden], dialog:not([open]), .sr-only, details:not([open]) > :not(summary)')) continue;
    if (r.height < 43.5 || (r.width < 43.5 && !['INPUT','SELECT','TEXTAREA'].includes(el.tagName))) bad.push(`${el.tagName.toLowerCase()}.${el.className}:${(el.textContent||el.name||'').trim().slice(0,30)} ${Math.round(r.width)}x${Math.round(r.height)}`);
  }
  return bad.slice(0, 8);
}"""


def password_for(user: str, role_hint: str | None = None) -> str:
    f = os.environ.get("KARESHAFAA_DEMO_CREDENTIALS_FILE")
    if f and Path(f).exists():
        for line in Path(f).read_text().splitlines():
            if line and not line.startswith("#"):
                u, _r, p = line.split("\t")
                if u == user:
                    return p
    return os.environ["KARESHAFAA_DEMO_PASSWORD"]


def main() -> int:
    devices = DEVICES if MODE == "all" else {k: DEVICES[k] for k in ("desktop-1280", "mobile-390", "iphone-15-pro", "pixel-7", "laptop")}
    problems: list[str] = []
    results = []
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        # login page itself
        orientations = []
        for dn, (w, h, mob, sc) in devices.items():
            orientations.append((dn, w, h, mob, sc))
            if MODE == "all" and dn in LANDSCAPE:
                orientations.append((dn + "-landscape", h, w, mob, sc))
        for dn, w, h, mob, sc in orientations:
            ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=min(sc, 2), is_mobile=mob, has_touch=mob)
            p = ctx.new_page()
            p.goto(BASE + "/login", wait_until="networkidle")
            sw, iw = p.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
            if sw > iw:
                problems.append(f"{dn} login: horizontal overflow {sw}>{iw}")
            if mob:
                bad = p.evaluate(TAP_JS)
                if bad:
                    problems.append(f"{dn} login: small tap targets {bad}")
            p.screenshot(path=str(OUT / f"{dn}_login.png"), full_page=True)
            ctx.close()
            for user, pages in PLAN.items():
                ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=min(sc, 2), is_mobile=mob, has_touch=mob)
                p = ctx.new_page()
                errs: list[str] = []
                p.on("pageerror", lambda e, u=user: errs.append(f"JS error {e}"))
                p.on("response", lambda r: errs.append(f"{r.status} {r.url}") if "/api/" in r.url and r.status >= 400 else None)
                p.goto(BASE + "/login", wait_until="networkidle")
                p.fill("#username", user)
                p.fill("#password", password_for(user))
                p.click("button[type=submit]")
                p.wait_for_load_state("networkidle")
                case_id = None
                for name, path in pages:
                    if "{case}" in path:
                        if case_id is None:
                            r = ctx.request.get(BASE + "/api/queue?view=all&limit=1")
                            case_id = r.json()["items"][0]["id"]
                        path = path.replace("{case}", str(case_id))
                    p.goto(BASE + path, wait_until="networkidle")
                    p.wait_for_timeout(600)
                    sw, iw = p.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
                    row = {"device": dn, "user": user, "page": name, "scrollWidth": sw, "innerWidth": iw}
                    if sw > iw:
                        problems.append(f"{dn} {user} {name}: horizontal overflow {sw}>{iw}")
                    if mob:
                        bad = p.evaluate(TAP_JS)
                        row["small_targets"] = bad
                        if bad:
                            problems.append(f"{dn} {user} {name}: small tap targets {bad}")
                    results.append(row)
                    p.screenshot(path=str(OUT / f"{dn}_{name}.png"), full_page=True)
                for e in errs:
                    problems.append(f"{dn} {user}: {e}")
                ctx.close()
        b.close()
    (OUT / "responsive_report.json").write_text(json.dumps({"problems": problems, "checks": results}, indent=1))
    print(f"{len(results)} page checks on {len(orientations)} device/orientations; {len(problems)} problems")
    for x in problems[:80]:
        print(" -", x)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
