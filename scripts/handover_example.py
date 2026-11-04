"""Handover example: one repeatable run, end to end, on a FRESH throwaway database.

1. Build a new local SQLite database with synthetic data only (fixed seed).
2. Send the two sample inputs in docs/handover/examples/ (HL7 v2 ORU^R01 and a FHIR Bundle) through the real API.
3. For each resulting case record the alert decision: rule, urgency tier, pathway target, the current step and its
   SLA timer, then run the escalation sweep at fixed offsets (+1 day, +40 days) to show the alert ladder.
4. Export the case as FHIR R4 and validate every resource (fhir.resources, R4B models), and check the audit chain.
5. Fixed-seed evaluation: rules engine vs the independent reference on 20,000 randomised results.
6. Write the run record as JSON and Markdown.

Usage: python scripts/handover_example.py OUT_DIR   (wrapped by scripts/handover_example.sh)
Never touches data/kareshafaa.db: every path lives under OUT_DIR.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EX = ROOT / "docs" / "handover" / "examples"
SEED = 2026
N_PATIENTS = 300


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "handover_run").resolve()
    out.mkdir(parents=True, exist_ok=True)
    db_path = out / "handover_example.db"
    if db_path.exists():
        db_path.unlink()
    os.environ.update({
        "KARESHAFAA_ENV": "test", "KARESHAFAA_DATABASE_URL": f"sqlite:///{db_path}",
        "KARESHAFAA_DATA_DIR": str(out), "KARESHAFAA_MODEL_DIR": str(out / "models"),
        "KARESHAFAA_SECRET_KEY": os.urandom(24).hex(), "KARESHAFAA_DEMO_LOGINS": "true",
        "KARESHAFAA_SWEEP_INTERVAL_SECONDS": "0", "KARESHAFAA_DATA_STEPS": "off",
        "KARESHAFAA_DEMO_CREDENTIALS_FILE": str(out / "throwaway_credentials.txt"),
    })
    sys.path.insert(0, str(ROOT))
    t0 = time.time()
    from kareshafaa.seed import seed

    seeded = seed(N_PATIENTS, SEED, train=True, verbose=False, simulate=False)
    from fastapi.testclient import TestClient
    from fhir.resources.R4B import get_fhir_model_class
    from sqlalchemy import select

    from kareshafaa.db import session_factory
    from kareshafaa.main import create_app
    from kareshafaa.models import Case, Escalation, Patient, utcnow
    from kareshafaa.services.escalation import run_sweep

    db = session_factory()()
    # the sample files name SYN-100001 / SYN-100002; point them at two seeded patients with no open case
    open_ids = select(Case.patient_id).where(Case.state != "closed")
    pats = db.scalars(select(Patient).where(Patient.id.not_in(open_ids), Patient.deceased.is_(False),
                                            Patient.birth_year <= 1980).order_by(Patient.id).limit(2)).all()
    hl7 = (EX / "input_oru_r01_fit_positive.hl7").read_text()
    hl7 = hl7.replace("SYN-100001", pats[0].mrn).replace("19740315", f"{pats[0].birth_year}0315").replace("\r\n", "\r").replace("\n", "\r")
    bundle = json.loads((EX / "input_fhir_bundle_hba1c.json").read_text())
    bundle["entry"][0]["resource"]["identifier"][0]["value"] = pats[1].mrn

    record: dict = {"run": {"script": "scripts/handover_example.sh", "seed": SEED, "synthetic_patients": N_PATIENTS,
                            "database": str(db_path.name), "python": platform.python_version(),
                            "git_commit": _git("rev-parse", "--short", "HEAD"),
                            "started": utcnow().isoformat(timespec="seconds") + "Z",
                            "data": "synthetic only - not real patients"},
                    "seed_summary": {k: v for k, v in seeded.items() if isinstance(v, (int, float, str))},
                    "inputs": [], "validation": {}, "evaluation": {}}
    with TestClient(create_app()) as c:
        H, L = _login(c, "aisha.rahman"), _login(c, "fatima.al.mansoori")
        results = [("HL7 v2 ORU^R01", "input_oru_r01_fit_positive.hl7",
                    c.post("/api/ingest/hl7v2", headers=H, content=hl7.encode())),
                   ("FHIR R4 Bundle", "input_fhir_bundle_hba1c.json", c.post("/api/ingest/fhir", headers=H, json=bundle))]
        fhir_ok, fhir_n, fhir_types = True, 0, set()
        for label, fname, r in results:
            assert r.status_code == 200, r.text
            body = r.json()
            entry = {"format": label, "file": f"docs/handover/examples/{fname}", "message_id": body["message_id"],
                     "ingest_status": body["status"], "actionable": body["actionable"]}
            if "ack" in body:
                entry["hl7_ack"] = body["ack"].replace("\r", "\n")
            if body["case"]:
                cid = body["case"]["id"]
                d = c.get(f"/api/cases/{cid}", headers=L).json()
                case = d.get("case", d)
                entry["alert"] = {k: case.get(k) for k in ("id", "programme_name", "tier", "state_label", "rule_id",
                                                           "pathway", "target", "target_due_at", "review_required",
                                                           "review_reasons") if k in case}
                cur = d.get("sla") or case.get("sla")
                steps = d.get("steps") or case.get("steps") or []
                entry["sla_timer"] = {"current": cur, "steps": [{k: s.get(k) for k in ("state", "label", "due_at",
                                                                                       "sla_hours", "sla_status")}
                                                                for s in steps][-3:]}
                ladder = []
                for days in (1, 40):
                    sw = run_sweep(db, now=utcnow() + timedelta(days=days), case_ids=[cid])
                    db.commit()
                    esc = db.scalars(select(Escalation).where(Escalation.case_id == cid).order_by(Escalation.level)).all()
                    ladder.append({"sweep_at": f"+{days} days", "summary": {k: v for k, v in sw.items() if v},
                                   "escalations": [{"level": e.level, "kind": e.kind} for e in esc]})
                entry["alert_ladder"] = ladder
                b = c.get(f"/api/cases/{cid}/fhir", headers=L).json()
                try:
                    get_fhir_model_class("Bundle").model_validate(b)
                    for e in b["entry"]:
                        get_fhir_model_class(e["resource"]["resourceType"]).model_validate(e["resource"])
                        fhir_n += 1
                        fhir_types.add(e["resource"]["resourceType"])
                except Exception as ex:  # noqa: BLE001
                    fhir_ok = False
                    entry["fhir_error"] = str(ex)[:300]
                (out / f"case_{cid}_fhir_bundle.json").write_text(json.dumps(b, indent=1))
                entry["fhir_export"] = f"case_{cid}_fhir_bundle.json"
            record["inputs"].append(entry)
        record["validation"]["fhir"] = {"valid": fhir_ok, "resources_validated": fhir_n,
                                        "resource_types": sorted(fhir_types), "validator": "fhir.resources R4B models"}
        record["validation"]["audit_chain"] = c.get("/api/audit/verify", headers=_login(c, _admin_name())).json()
    db.close()
    diff_out = out / "differential_20000.md"
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "differential_test.py"), "20000", "--seed", "20262020",
                        "--workers", "2", "--out", str(diff_out)], capture_output=True, text=True, cwd=ROOT)
    dj = json.loads(diff_out.with_suffix(".json").read_text())
    record["evaluation"]["rules_vs_reference"] = {k: dj[k] for k in ("n", "seed", "agree", "safety_critical",
                                                                       "non_critical", "safety_upper95")} | {"exit_code": p.returncode}
    record["run"]["seconds"] = round(time.time() - t0, 1)
    (out / "run_record.json").write_text(json.dumps(record, indent=1, default=str))
    (out / "run_record.md").write_text(_markdown(record))
    print(json.dumps({"out": str(out), "seconds": record["run"]["seconds"], "fhir_valid": fhir_ok,
                      "audit_chain": record["validation"]["audit_chain"], "safety_critical": dj["safety_critical"]}))
    ok = fhir_ok and dj["safety_critical"] == 0 and all(i["actionable"] for i in record["inputs"])
    return 0 if ok else 1


def _login(c, username: str) -> dict:
    t = c.post("/api/auth/demo-login", json={"username": username}).json()["token"]
    c.cookies.clear()
    return {"Authorization": f"Bearer {t}"}


def _admin_name() -> str:
    from sqlalchemy import select

    from kareshafaa.db import session_factory
    from kareshafaa.models import User

    with session_factory()() as s:
        return s.scalar(select(User.username).where(User.role == "admin").order_by(User.id).limit(1))


def _git(*a) -> str:
    try:
        return subprocess.run(["git", *a], capture_output=True, text=True, cwd=ROOT).stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def _markdown(r: dict) -> str:
    L = ["# KareShafaa handover example: run record", "",
         f"Synthetic data only. Seed {r['run']['seed']}, {r['run']['synthetic_patients']} synthetic patients, commit "
         f"`{r['run']['git_commit']}`, started {r['run']['started']}, {r['run']['seconds']} s.", ""]
    for i in r["inputs"]:
        a = i.get("alert", {})
        L += [f"## Input: {i['format']} (`{i['file']}`)", "",
              f"* Ingest status: **{i['ingest_status']}**; actionable: **{i['actionable']}**; message id `{i['message_id']}`"]
        if a:
            L += [f"* Alert: case {a.get('id')}, {a.get('programme_name')}, tier **{a.get('tier')}**, rule `{a.get('rule_id')}`",
                  f"* Pathway: {a.get('pathway')}; target {a.get('target')} (due {a.get('target_due_at')})",
                  f"* State now: {a.get('state_label')}; human review required: {a.get('review_required')}"]
            cur = (i.get("sla_timer") or {}).get("current")
            if cur:
                L.append(f"* SLA timer on the current step: {json.dumps(cur)}")
            for s in i.get("alert_ladder", []):
                L.append(f"* Sweep at {s['sweep_at']}: {json.dumps(s['summary'])}; escalation levels so far "
                         f"{[e['level'] for e in s['escalations']]}")
            L.append(f"* FHIR export: `{i.get('fhir_export')}`")
        if i.get("hl7_ack"):
            L += ["", "HL7 ACK returned:", "", "```", i["hl7_ack"], "```"]
        L.append("")
    f, a, e = r["validation"]["fhir"], r["validation"]["audit_chain"], r["evaluation"]["rules_vs_reference"]
    L += ["## Checks", "",
          f"* FHIR: {f['resources_validated']} resources validated ({', '.join(f['resource_types'])}) - valid: **{f['valid']}**",
          f"* Audit chain: {json.dumps(a)}",
          f"* Rules engine vs independent reference: {e['n']:,} randomised results (seed {e['seed']}): agree {e['agree']:,}, "
          f"safety-critical disagreements **{e['safety_critical']}** (95 % upper bound {e['safety_upper95']:.1e})", ""]
    return "\n".join(L)


if __name__ == "__main__":
    raise SystemExit(main())
