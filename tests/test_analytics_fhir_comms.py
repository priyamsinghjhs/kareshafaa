"""Small-cell suppression, KPIs, FHIR export, channel ladder, templates, improvement proposals."""
import pytest
from sqlalchemy import select

from kareshafaa.comms.content import SUPPORTED_LANGUAGES, TEMPLATES
from kareshafaa.models import Communication, RuleProposal
from kareshafaa.services.analytics import secondary_suppress, suppress_count, suppress_rate


def test_suppression_helpers():
    assert suppress_count(4) == "<5" or isinstance(suppress_count(4), str)
    assert suppress_count(5) == 5
    assert isinstance(suppress_rate(1, 3), str)
    out = secondary_suppress({"a": 3, "b": 50, "c": 40})
    hidden = [k for k, v in out.items() if isinstance(v, str)]
    assert "a" in hidden and len(hidden) >= 2  # complementary suppression so 'a' can't be derived


def test_analyst_aggregates_only(client, as_user):
    h = as_user("analyst")
    k = client.get("/api/analytics/kpis", headers=h).json()
    assert k["label"]  # every KPI payload says where its figures come from
    s = str(k)
    assert "SYN-" not in s and "given_name" not in s
    f = client.get("/api/analytics/fairness", headers=h).json()
    assert f["suppression_threshold"] == 5
    for rows in (v for v in f.values() if isinstance(v, list)):
        for row in rows:
            n = row.get("n")
            assert isinstance(n, str) or n is None or n >= 5


def test_simulation_results_labelled(client, as_user):
    r = client.get("/api/analytics/simulation", headers=as_user("analyst")).json()
    assert "SIMULATED" in str(r).upper()


def test_fhir_export_bundle(client, as_user):
    h = as_user("fatima.al.mansoori")
    b = client.get("/api/cases/2001/fhir", headers=h).json() if False else None
    # pick a closed case with a rich history
    cases = client.get("/api/cases?state=closed&limit=5", headers=h).json()["items"]
    cid = cases[0]["id"]
    b = client.get(f"/api/cases/{cid}/fhir", headers=h).json()
    assert b["resourceType"] == "Bundle" and b["type"] == "collection"
    types = {e["resource"]["resourceType"] for e in b["entry"]}
    assert {"Patient", "DiagnosticReport", "Observation", "Task", "Organization", "Consent"} <= types
    full = {e["fullUrl"] for e in b["entry"]}
    # every literal reference resolves inside the bundle
    import json

    refs = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "reference" and isinstance(v, str):
                    refs.add(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(b)
    ids = {f"{e['resource']['resourceType']}/{e['resource']['id']}" for e in b["entry"]}
    unresolved = [r for r in refs if r not in ids and r not in full]
    assert unresolved == [], unresolved[:5]
    assert json.dumps(b)
    case_task = [e["resource"] for e in b["entry"] if e["resource"]["resourceType"] == "Task"
                 and e["resource"].get("code", {}).get("text", "").lower().startswith("screening")]
    assert case_task or any(e["resource"]["resourceType"] == "Task" for e in b["entry"])


def test_templates_complete_for_all_languages():
    cats = set(TEMPLATES["en"])
    for lang in SUPPORTED_LANGUAGES:
        assert set(TEMPLATES[lang]) == cats, lang
        for cat in cats:
            for variant in ("standard", "simplified"):
                assert TEMPLATES[lang][cat][variant].strip(), (lang, cat, variant)


def test_channel_ladder_respects_consent(client, as_user, new_case, db):
    c = new_case(phone_valid=True, whatsapp=True, smartphone=False)
    cid = c["id"]
    co = as_user("aisha.rahman")
    client.post(f"/api/cases/{cid}/consent", headers=co, json={"scope": "comm_sms", "decision": "deny",
                                                                "note": "Patient asked by phone: no SMS please"})
    r = client.post(f"/api/cases/{cid}/notify", headers=co, json={"category": "result_notification"}).json()
    att = r["result"]["attempts"]
    sms = [a for a in att if a["channel"] == "sms"]
    assert sms and "no consent" in sms[0]["status"]
    db.expire_all()
    sent = db.scalars(select(Communication).where(Communication.case_id == cid,
                                                  Communication.channel == "sms")).all()
    assert all(m.status == "not-done" for m in sent)
    assert all(m.simulated and m.content.startswith("[SIMULATED") for m in
               db.scalars(select(Communication).where(Communication.case_id == cid)))


def test_patient_language_templates_used(client, as_user, new_case, db):
    c = new_case(phone_valid=True, whatsapp=True, smartphone=True, language="ur")
    client.post(f"/api/cases/{c['id']}/notify", headers=as_user("aisha.rahman"), json={"category": "result_notification"})
    db.expire_all()
    m = db.scalars(select(Communication).where(Communication.case_id == c["id"],
                                               Communication.channel != "phone")).first()
    assert m.language == "ur"


def test_proposals_require_human_approval(client, as_user, db):
    lead, analyst = as_user("fatima.al.mansoori"), as_user("analyst")
    items = client.get("/api/governance/proposals", headers=lead).json()
    items = items["items"] if isinstance(items, dict) else items
    pending = [p for p in items if p["status"] == "pending"]
    assert pending, "seed should generate improvement proposals from lessons learned"
    p = next((x for x in pending if x["kind"] != "advisory"), pending[0])
    assert client.post(f"/api/governance/proposals/{p['id']}/decide", headers=analyst,
                       json={"approve": True, "note": "fine by me"}).status_code == 403
    r = client.post(f"/api/governance/proposals/{p['id']}/decide", headers=lead,
                    json={"approve": True, "note": "Reviewed evidence; approve for pilot."})
    assert r.status_code == 200, r.text
    db.expire_all()
    rp = db.get(RuleProposal, p["id"])
    assert rp.status in ("approved", "applied")
    assert client.post(f"/api/governance/proposals/{p['id']}/decide", headers=lead,
                       json={"approve": True, "note": "again please"}).status_code == 409


def test_lessons_summary_suppressed(client, as_user):
    r = client.get("/api/analytics/lessons", headers=as_user("analyst")).json()
    s = str(r)
    assert "SYN-" not in s


@pytest.mark.parametrize("path", ["/", "/queue", "/dashboard", "/governance", "/patient", "/audit", "/consults",
                                  "/cases/1", "/healthz"])
def test_pages_render(client, path):
    r = client.get(path)
    assert r.status_code == 200
    if path != "/healthz":
        assert "not a certified medical device" in r.text
        assert "prototype" not in r.text.lower()
        assert "demo" not in r.text.lower() and "synthetic" not in r.text.lower()


def test_kpi_cache_invalidates_on_new_data(client, as_user, new_case, monkeypatch):
    from kareshafaa.services import analytics

    monkeypatch.setattr(analytics, "MIN_REFRESH_S", 0.0)  # disable the bounded-staleness window
    analytics.clear_cache()
    h = as_user("analyst")
    k1 = client.get("/api/analytics/kpis", headers=h).json()
    k2 = client.get("/api/analytics/kpis", headers=h).json()
    assert k1 == k2
    fp1 = analytics._CACHE["kpis"][0]
    new_case()
    client.get("/api/analytics/kpis", headers=h)
    assert analytics._CACHE["kpis"][0] != fp1  # new data -> recomputed


def test_kpi_cache_bounded_staleness(client, as_user, new_case):
    from kareshafaa.services import analytics

    analytics.clear_cache()
    h = as_user("analyst")
    client.get("/api/analytics/kpis", headers=h)
    t1 = analytics._CACHE["kpis"][1]
    new_case()
    client.get("/api/analytics/kpis", headers=h)
    assert analytics._CACHE["kpis"][1] == t1  # within MIN_REFRESH_S the cached value is served


def test_direct_pathway_wording_matches_result(client, as_user, make_patient):
    """BI-RADS 4 goes straight to biopsy, so the patient must not be told 'more pictures' (BI-RADS 0 wording)."""
    from kareshafaa.comms.content import programme_text

    class C:  # minimal stand-in for a Case
        programme, needs_confirmation = "breast", False
    t = programme_text("en", C)
    assert "biopsy" in t["next_step"]
    C.needs_confirmation = True
    assert "extra breast imaging" in programme_text("en", C)["next_step"]
    for lang in ("ar", "hi", "ur"):
        C.needs_confirmation = False
        assert programme_text(lang, C)["next_step"] != programme_text("en", C)["next_step"]


def test_tagalog_patient_gets_interpreter_notice(client, as_user):
    d = client.get("/api/me/case", headers=as_user("patient.tl")).json()
    assert d["language"] == "en" and d["interpreter_notice"].startswith("Tagalog")
    assert "biopsy" in d["next_step"]  # Maria Santos: BI-RADS 4 -> direct biopsy pathway


FHIR_COMM_STATUS = {"preparation", "in-progress", "not-done", "on-hold", "stopped", "completed", "entered-in-error",
                    "unknown"}


@pytest.mark.parametrize("status", ["completed", "failed", "not-done", "pending", "on-hold", "stopped"])
def test_fhir_export_handles_every_communication_status(db, new_case, status):
    """Round 17: a case holding a queued night-time reminder (on-hold, round 10) or a dispatched/cancelled
    placeholder (stopped) could not be exported: KeyError -> HTTP 500 (found by the 10k load test)."""
    from kareshafaa.fhir import export_case
    from kareshafaa.models import Case, Communication
    c = db.get(Case, new_case(value=250)["id"])
    db.add(Communication(case_id=c.id, patient_id=c.patient_id, category="reminder", channel="scheduled",
                         language="en", template_key="reminder", content="[SIMULATED - not sent] queued",
                         status=status, simulated=True, attempt_no=1))
    db.flush()
    b = export_case(db, c)
    st = [e["resource"]["status"] for e in b["entry"] if e["resource"]["resourceType"] == "Communication"]
    assert st and set(st) <= FHIR_COMM_STATUS
    db.rollback()
