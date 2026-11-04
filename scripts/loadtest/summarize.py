"""Markdown summary of a load-test run: python scripts/loadtest/summarize.py /tmp/lt 500 5000 20000"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def load(p: Path) -> dict:
    return json.loads(p.read_text()) if p.exists() else {}


def main() -> int:
    root = Path(sys.argv[1])
    scales = sys.argv[2:]
    data = {s: {"seed": load(root / s / "seed_summary.json"), "ip": load(root / s / "inprocess.json"),
                "c2": load(root / s / "http_ttl2.json"), "c0": load(root / s / "http_ttl0.json")} for s in scales}
    out = ["| Scale (open cases) | " + " | ".join(f"{data[s]['ip'].get('open_cases', s)}" for s in scales) + " |",
           "|---|" + "---|" * len(scales)]

    def row(label, f):
        vals = []
        for s in scales:
            try:
                vals.append(f(data[s]))
            except (KeyError, TypeError, IndexError):
                vals.append("–")
        out.append(f"| {label} | " + " | ".join(vals) + " |")

    row("rows: patients / cases / steps / comms", lambda x: "{} / {} / {} / {}".format(
        *(x["seed"]["rows"][k] for k in ("patients", "cases", "step_tasks", "communications"))))
    row("rows: questionnaire responses / lessons", lambda x: "{} / {}".format(
        x["seed"]["rows"]["questionnaire_responses"], x["seed"]["rows"]["lessons"]))
    q = "GET /api/queue (coordinator, page 50)"
    row("work_queue() service, median ms / SQL / peak MB", lambda x: "{} / {} / {}".format(
        x["ip"]["work_queue"]["median_ms"], x["ip"]["work_queue"]["sql"], x["ip"]["work_queue"]["tracemalloc_peak_mb"]))
    for lab, key in (("cache off", "c0"), ("2 s cache (default)", "c2")):
        row(f"queue 1 user, p50 / p95 ms ({lab})", lambda x, k=key: "{} / {}".format(
            x[k]["sequential"][q]["p50"], x[k]["sequential"][q]["p95"]))
        for conc in ("10", "25"):
            row(f"queue {conc} users, p50 / p95 ms, ok/total ({lab})", lambda x, k=key, c=conc: "{} / {} ({}/{})".format(
                x[k]["concurrent"][c]["p50"], x[k]["concurrent"][c]["p95"], x[k]["concurrent"][c]["ok"],
                x[k]["concurrent"][c]["requests"]))
    for name in ("GET /api/queue?view=review (GP)", "GET /api/cases/{id}", "GET /api/analytics/kpis (dashboard)",
                 "GET /api/analytics/impact (dashboard)", "GET /api/patients?q=SYN-", "GET /api/recalls?view=upcoming",
                 "GET /queue (HTML)", "GET /cases/{id} (HTML)", "GET /dashboard (HTML)"):
        row(f"{name}: p50 / p95 ms / SQL", lambda x, n=name: "{} / {} / {}".format(
            x["c0"]["sequential"][n]["p50"], x["c0"]["sequential"][n]["p95"], x["ip"]["endpoints"][n]["sql"]))
    for k in ("kpis_uncached", "impact_uncached"):
        row(f"dashboard {k.split('_')[0]}() without aggregate cache: ms / SQL", lambda x, k=k: "{} / {}".format(
            x["ip"][k]["median_ms"], x["ip"][k]["sql"]))
    row(f"{q}: SQL statements (endpoint)", lambda x: str(x["ip"]["endpoints"][q]["sql"]))
    row("generate_proposals(): median / max ms", lambda x: "{} / {}".format(
        x["ip"]["generate_proposals"]["median_ms"], x["ip"]["generate_proposals"]["p95_ms"]))
    row("generate_proposals(): SQL / proposals", lambda x: "{} / {}".format(
        x["ip"]["generate_proposals"]["sql"], x["ip"]["generate_proposals"]["proposals"]))
    row("generate_proposals(): peak MB (tracemalloc / RSS delta)", lambda x: "{} / {}".format(
        x["ip"]["generate_proposals"]["tracemalloc_peak_mb"], x["ip"]["generate_proposals"]["rss_peak_delta_mb"]))
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
