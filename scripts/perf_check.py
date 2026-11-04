"""Mobile page-performance check (dev only; Playwright + local Chrome). Never run against the hosted service.

Emulates a mid-range phone (412x823, 4x CPU slowdown) on a "slow 4G" link (150 ms RTT, 1.6 Mbit/s down) and loads
key pages twice: cold (empty browser cache) and warm (repeat visit). Reports time to first byte, first contentful
paint, largest contentful paint, DOMContentLoaded, load, bytes transferred and requests.

Usage: KARESHAFAA_DEMO_CREDENTIALS_FILE=... python scripts/perf_check.py http://127.0.0.1:8044 out.json [--fast]
(--fast: no network/CPU throttling, to measure the server alone). Passwords come from the file, never argv.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1]
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else None
FAST = "--fast" in sys.argv
RUNS = 3
PAGES = [  # (name, user or None, path)
    ("login", None, "/login"),
    ("coordinator queue", "aisha.rahman", "/queue"),
    ("case", "aisha.rahman", "/cases/{case}"),
    ("lead outcomes", "fatima.al.mansoori", "/dashboard"),
    ("patient app", "patient.en", "/patient"),
]
METRICS_JS = """() => new Promise(res => {
  let lcp = 0;
  try { new PerformanceObserver(l => { for (const e of l.getEntries()) lcp = Math.max(lcp, e.startTime); })
        .observe({ type: 'largest-contentful-paint', buffered: true }); } catch (e) {}
  setTimeout(() => {
    const n = performance.getEntriesByType('navigation')[0];
    const fcp = (performance.getEntriesByName('first-contentful-paint')[0] || {}).startTime || null;
    const rs = performance.getEntriesByType('resource');
    const bytes = (n.transferSize || 0) + rs.reduce((a, r) => a + (r.transferSize || 0), 0);
    const decoded = (n.decodedBodySize || 0) + rs.reduce((a, r) => a + (r.decodedBodySize || 0), 0);
    res({ ttfb: n.responseStart - n.requestStart, fcp, lcp: lcp || fcp, dcl: n.domContentLoadedEventEnd,
          load: n.loadEventEnd, bytes, decoded, requests: rs.length + 1 });
  }, 400);
})"""


def password_for(user: str) -> str:
    for line in Path(os.environ["KARESHAFAA_DEMO_CREDENTIALS_FILE"]).read_text().splitlines():
        if line and not line.startswith("#"):
            u, _r, p = line.split("\t")
            if u == user:
                return p
    return os.environ["KARESHAFAA_DEMO_PASSWORD"]


def throttle(page) -> object:
    cdp = page.context.new_cdp_session(page)
    cdp.send("Network.enable")
    if not FAST:
        cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 150,
                                                      "downloadThroughput": 1.6e6 / 8, "uploadThroughput": 750e3 / 8})
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": 4})
    return cdp


def main() -> int:
    res: dict[str, dict] = {}
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        case_id = None
        for name, user, path in PAGES:
            cold, warm = [], []
            for _ in range(RUNS):
                ctx = b.new_context(viewport={"width": 412, "height": 823}, device_scale_factor=1.75, is_mobile=True,
                                    has_touch=True, service_workers="block")
                ctx.add_init_script("for (const r of ['coordinator','gp','specialist','clinical_lead','analyst','admin','translator']) localStorage.setItem('ks-guide-'+r+'-v1','1');")
                p = ctx.new_page()
                if user:
                    p.goto(BASE + "/login", wait_until="networkidle")
                    p.fill("#username", user)
                    p.fill("#password", password_for(user))
                    p.click("button[type=submit]")
                    p.wait_for_load_state("networkidle")
                    if "{case}" in path and case_id is None:
                        case_id = ctx.request.get(BASE + "/api/queue?view=all&limit=1").json()["items"][0]["id"]
                cdp = throttle(p)
                cdp.send("Network.clearBrowserCache")
                url = BASE + path.replace("{case}", str(case_id))
                p.goto(url, wait_until="load")
                p.wait_for_load_state("networkidle")
                cold.append(p.evaluate(METRICS_JS))
                p.goto(url, wait_until="load")  # repeat visit: browser cache now warm
                p.wait_for_load_state("networkidle")
                warm.append(p.evaluate(METRICS_JS))
                ctx.close()
            med = lambda rows, k: round(statistics.median([r[k] or 0 for r in rows]), 1)  # noqa: E731
            res[name] = {kind: {k: med(rows, k) for k in ("ttfb", "fcp", "lcp", "dcl", "load", "bytes", "decoded", "requests")}
                         for kind, rows in (("cold", cold), ("warm", warm))}
            c, w = res[name]["cold"], res[name]["warm"]
            print(f"{name:18s} cold: TTFB {c['ttfb']:6.0f} ms  FCP {c['fcp']:6.0f}  LCP {c['lcp']:6.0f}  load {c['load']:6.0f}  "
                  f"{c['bytes'] / 1024:6.1f} KB/{c['requests']:.0f} req | warm: FCP {w['fcp']:6.0f}  load {w['load']:6.0f}  {w['bytes'] / 1024:6.1f} KB")
        b.close()
    if OUT:
        OUT.write_text(json.dumps({"throttled": not FAST, "runs": RUNS, "pages": res}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
