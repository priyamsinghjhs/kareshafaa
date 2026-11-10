"""Impact report, explainability (why this priority, rule cards), override register, data protection and retention,
FHIR CapabilityStatement, readiness, API reference access, JSON logging and the About page."""
from __future__ import annotations

import csv
import io
import json
import logging
from datetime import timedelta

import pytest

LEAD, ADMIN, ANALYST, COORD, GP = "fatima.al.mansoori", "admin", "analyst", "aisha.rahman", "omar.haddad"


# ---------------------------------------------------------------- impact report
def test_impact_report_figures_and_synthetic_label(client, as_user, new_case):
    new_case()
    r = client.get("/api/analytics/impact", headers=as_user(LEAD))
    assert r.status_code == 200, r.text
    d = r.json()
    assert {"loop_closure_rate", "lost_to_follow_up_rate", "median_days_to_specialist", "within_target_rate",
            "recall_uptake_rate"} <= set(d["headline"])
    assert "synthetic" in d["data"]["label"].lower()
    assert [t["tier"] for t in d["by_tier"]] == [t for t in ("critical", "urgent", "high", "routine") if t in {x["tier"] for x in d["by_tier"]}]
    assert set(d["equity"]) == {"language", "nationality_group", "region", "age_band"}
    for e in d["equity"].values():
        for row in e["rows"]:
            assert row["loop_closure_rate"] is None or row["loop_closure_rate"] == "suppressed" or 0 <= row["loop_closure_rate"] <= 1


def test_impact_small_cells_suppressed():
    from kareshafaa.services.analytics import suppress_rate

    assert suppress_rate(2, 3, thr=5) == "suppressed"
    assert suppress_rate(10, 20, thr=5) == 0.5


def test_impact_programme_filter_and_validation(client, as_user):
    h = as_user(LEAD)
    assert client.get("/api/analytics/impact?programme=colorectal_fit", headers=h).json()["programme"] == "colorectal_fit"
    assert client.get("/api/analytics/impact?programme=nope", headers=h).status_code == 422


def test_impact_csv_export_is_audited_and_labelled(client, as_user, db):
    from sqlalchemy import func, select

    from kareshafaa.models import AuditEvent

    before = db.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.event == "analytics.impact_export"))
    r = client.get("/api/analytics/impact?format=csv", headers=as_user(LEAD))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[1][0] == "# data" and "synthetic" in rows[1][1].lower()
    assert ["section", "group_type", "group", "metric", "value"] in rows
    assert any(x[:2] == ["equity", "language"] for x in rows)
    db.expire_all()
    assert db.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.event == "analytics.impact_export")) == before + 1


@pytest.mark.parametrize("user,code", [(LEAD, 200), (ANALYST, 200), (ADMIN, 200), (COORD, 403), (GP, 403), ("patient.en", 403)])
def test_impact_access(client, as_user, user, code):
    assert client.get("/api/analytics/impact", headers=as_user(user)).status_code == code


def test_lost_to_follow_up_definition(db, make_patient):
    """A case far past its pathway target with no step change counts as lost; a fresh one does not."""
    from kareshafaa.models import Case, utcnow
    from kareshafaa.services import impact

    now = utcnow()
    c = db.query(Case).filter(Case.state != "closed", Case.target_due_at.is_not(None)).first()
    assert c is not None
    frame = impact._frame(db, None, now + timedelta(days=3650))
    row = frame[frame.id == c.id].iloc[0]
    assert bool(row.lost) and bool(row.mature)
    frame_now = impact._frame(db, None, c.created_at)
    assert not bool(frame_now[frame_now.id == c.id].iloc[0].mature)


# ---------------------------------------------------------------- explainability
def test_case_detail_explains_priority(client, as_user, new_case):
    c = new_case(value=250)
    d = client.get(f"/api/cases/{c['id']}", headers=as_user(COORD)).json()
    w = d["why"]
    assert w["tier"] == d["tier"]
    assert w["rule"]["id"] == "CRC-FIT-POS" and "80" in w["rule"]["input"] and w["rule"]["source"]
    assert w["steps"] and w["steps"][0].startswith("Rule CRC-FIT-POS")
    assert "only orders" in w["human_in_loop"] and "did not set the tier" in w["ml"]["role"]


def test_why_shows_clinician_override(client, as_user, new_case, db):
    from kareshafaa.models import Case

    c = new_case(programme="colorectal_fit", value=None, value_text="garbled")  # fail-safe: human review
    case = db.get(Case, c["id"])
    assert case.review_required
    r = client.post(f"/api/cases/{c['id']}/review", headers=as_user(GP), json={"note": "Repeat kit requested; lab confirms spoilt sample", "tier": "routine"})
    assert r.status_code == 200, r.text
    w = client.get(f"/api/cases/{c['id']}", headers=as_user(COORD)).json()["why"]
    assert w["decided_by"] == "rules engine, then a clinician"
    assert any("lowered" in s and "spoilt sample" in s for s in w["steps"])
    reg = client.get("/api/governance/overrides", headers=as_user(LEAD)).json()
    hit = [x for x in reg["items"] if x["case_id"] == c["id"]]
    assert hit and hit[0]["lowered_tier"] and "spoilt sample" in hit[0]["reason"]


def test_rule_cards_cover_every_rule_the_engine_uses(client, as_user):
    import re
    from pathlib import Path

    import kareshafaa.clinical_rules as cr
    from kareshafaa.services.explain import rule_catalogue

    src = Path(cr.__file__).read_text()
    used = set(re.findall(r'"((?:CRC|CX|BR|DM|GEN)-[A-Z0-9-]+)"', src))
    used |= {f"{p}-{t}" for p in ("DM-A1C", "DM-FPG") for t in ("NEG", "PRE", "DIAB", "HIGH", "CRIT")}
    cat = rule_catalogue()
    missing = sorted(u for u in used if u not in cat and not u.endswith("-"))
    assert not missing, missing
    d = client.get("/api/governance/rule-cards", headers=as_user(LEAD)).json()
    for k in ("purpose", "inputs", "limits", "known_biases", "human_override", "human_in_loop"):
        assert d[k]
    assert "nationality" in d["not_inputs"] and d["engine"]["state_machine_version"]
    assert sum(len(f["rules"]) for f in d["families"]) >= 40


@pytest.mark.parametrize("path,ok_users,denied", [
    ("/api/governance/rule-cards", (LEAD, ANALYST, ADMIN), (COORD, GP, "patient.en")),
    ("/api/governance/overrides", (LEAD, ADMIN), (ANALYST, COORD, GP, "patient.en")),
    ("/api/governance/data-protection", (LEAD, ANALYST, ADMIN), (COORD, "patient.en")),
])
def test_governance_view_access(client, as_user, path, ok_users, denied):
    for u in ok_users:
        assert client.get(path, headers=as_user(u)).status_code == 200, u
    for u in denied:
        assert client.get(path, headers=as_user(u)).status_code == 403, u


def test_data_protection_mapping_and_retention(client, as_user):
    d = client.get("/api/governance/data-protection", headers=as_user(LEAD)).json()
    names = " ".join(f["framework"] for f in d["frameworks"])
    for k in ("No. 2 of 2019", "45 of 2021", "ADHICS", "Health Information Exchange"):
        assert k in names
    assert all(f["url"].startswith("https://") for f in d["frameworks"])
    keep = {x["data"]: x for x in d["retention"]}
    assert any("25 years" in x["keep"] and not x["automatic"] for x in keep.values())
    assert "outside the UAE" in d["hosting"]["residency"]


def test_retention_purge_only_operational_data(db):
    from sqlalchemy import func, select

    from kareshafaa.models import AuditEvent, Case, LoginAttempt, utcnow
    from kareshafaa.services.retention import LOGIN_ATTEMPT_RETENTION_DAYS, purge_operational

    now = utcnow()
    old = LoginAttempt(username="zz.retention", ip="10.9.9.9", success=False,
                       at=now - timedelta(days=LOGIN_ATTEMPT_RETENTION_DAYS + 1))
    fresh = LoginAttempt(username="zz.retention", ip="10.9.9.9", success=False, at=now - timedelta(days=1))
    db.add_all([old, fresh])
    db.commit()
    cases = db.scalar(select(func.count(Case.id)))
    out = purge_operational(db, now=now, force=True)
    assert out["login_attempts"] >= 1
    db.expire_all()
    assert db.scalar(select(func.count(LoginAttempt.id)).where(LoginAttempt.username == "zz.retention")) == 1
    assert db.scalar(select(func.count(Case.id))) == cases
    assert db.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.event == "retention.purged")) >= 1
    assert purge_operational(db, now=now)["skipped"] == "ran recently"


# ---------------------------------------------------------------- interoperability, readiness, API reference
def test_fhir_capability_statement(client):
    r = client.get("/fhir/metadata")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/fhir+json")
    d = r.json()
    assert d["resourceType"] == "CapabilityStatement" and d["fhirVersion"] == "4.0.1" and d["kind"] == "instance"
    types = {x["type"] for x in d["rest"][0]["resource"]}
    assert {"Patient", "DiagnosticReport", "Observation", "Task", "CarePlan"} <= types
    assert {o["name"] for o in d["rest"][0]["operation"]} == {"ingest-bundle", "export-case"}


def test_readyz_and_healthz(client):
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json()["status"] == "ready" and r.json()["checks"]["database"] == "ok"
    assert client.get("/healthz").json()["status"] == "ok"


def test_public_api_docs_are_off_and_reference_is_admin_only(client, as_user):
    for p in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(p).status_code == 404, p
    assert client.get("/api/admin/about/openapi.json", headers=as_user(LEAD)).status_code == 403
    r = client.get("/api/admin/about/openapi.json", headers=as_user(ADMIN))
    assert r.status_code == 200 and "/api/cases/{case_id}" in r.json()["paths"]
    assert not [p for p in r.json()["paths"] if "demo" in p]


def test_about_page_and_api(client, as_user):
    a = client.get("/api/admin/about", headers=as_user(ADMIN)).json()
    assert {i["source"] for i in a["integrations"]["inbound"]} == {"hl7v2", "fhir"}
    assert any(m["item"] == "HL7-capable system" for m in a["malaffi"])
    assert any(e["hie"] == "Malaffi" and e["configured"] for e in a["emirates"])
    assert "No connection" in a["notes"]["malaffi"]
    assert not [g for g in a["api"] if "demo" in g["tag"].lower()]
    html = client.get("/admin/about", headers=as_user(ADMIN))
    assert html.status_code == 200 and 'id="arch"' in html.text and 'href="/admin/about"' in html.text
    for u in (LEAD, COORD, ANALYST):
        assert client.get("/admin/about", headers=as_user(u)).status_code == 403
        assert client.get("/api/admin/about", headers=as_user(u)).status_code == 403


def test_nav_limit_still_five():
    from kareshafaa.ui import NAV

    assert all(len(v) <= 5 for v in NAV.values())
    assert "/admin/about" in [h for h, _, _ in NAV["admin"]]
    assert not any(h.startswith("/admin") for h, _, _ in NAV["clinical_lead"])


def test_json_log_format(capsys):
    from kareshafaa.logjson import JsonFormatter

    rec = logging.LogRecord("kareshafaa", logging.INFO, __file__, 1, "sweep %s", ("done",), None)
    d = json.loads(JsonFormatter().format(rec))
    assert d["msg"] == "sweep done" and d["level"] == "INFO" and d["ts"].endswith("Z")


def test_new_pages_have_no_demo_wording(client, as_user):
    for path, u in (("/dashboard", LEAD), ("/governance", LEAD), ("/admin/about", ADMIN)):
        assert "demo" not in client.get(path, headers=as_user(u)).text.lower().replace("demographic", ""), path


def test_first_run_guide_covers_every_staff_role(client, as_user):
    import re
    from pathlib import Path

    from kareshafaa.ui import HOME

    js = (Path(__file__).resolve().parents[1] / "kareshafaa/static/js/tour.js").read_text()
    guides = dict(re.findall(r"(\w+): \{ home: \"([^\"]+)\"", js))
    for role, home in HOME.items():
        if role == "patient":
            continue
        assert role in guides, role
        assert home.split("?")[0] == guides[role], role
    assert 'src="/static/js/tour.js?v=' in client.get("/queue", headers=as_user(COORD)).text


def test_why_explains_review_flag_and_kept_tier(client, as_user, new_case, db):
    from kareshafaa.models import Case

    c = new_case(programme="colorectal_fit", value=None, value_text="garbled")
    tier = db.get(Case, c["id"]).tier
    w = client.get(f"/api/cases/{c['id']}", headers=as_user(COORD)).json()["why"]
    assert any(s.startswith("Flagged for a clinician's review") and "until a clinician decides" in s for s in w["steps"]) or w["review"]["required"]
    r = client.post(f"/api/cases/{c['id']}/review", headers=as_user(GP), json={"note": "Checked with the lab; keep the tier.", "tier": tier})
    assert r.status_code == 200, r.text
    w = client.get(f"/api/cases/{c['id']}", headers=as_user(COORD)).json()["why"]
    assert w["decided_by"] == "rules engine, confirmed by a clinician"
    assert any("reviewed it and kept" in s and "keep the tier”." in s for s in w["steps"])


def test_impact_barrier_analysis(client, as_user):
    r = client.get("/api/analytics/impact", headers=as_user(LEAD))
    assert r.status_code == 200, r.text
    b = r.json()["barriers"]
    assert {"patient_reported", "patient_side", "system_side", "split", "note"} <= set(b)
    for row in b["patient_reported"] + b["patient_side"] + b["system_side"]:
        assert row["label"] and (isinstance(row["events"], str) and row["events"].startswith("<") or row["events"] >= 0)
    s = b["split"]
    if isinstance(s["patient_side_events"], int) and isinstance(s["system_side_events"], int) and s["system_side_events"]:
        assert abs(s["system_share"] - s["system_side_events"] / (s["patient_side_events"] + s["system_side_events"])) < 0.01
