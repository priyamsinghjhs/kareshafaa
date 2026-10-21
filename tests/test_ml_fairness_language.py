"""Round 18: the language recall gate, promotion refusal for gate-failing candidates, the interpreter-need input and
a staff-readable rationale for every model input. The ML only orders outreach within a tier."""
import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from wasl.ml import registry
from wasl.ml.calibration import _label
from wasl.ml.features import CAT_FEATURES, FAIRNESS_ATTRS, FEATURES, NUM_FEATURES, case_frame, language_unsupported
from wasl.models import Case, ModelVersion, User
from wasl.security import Actor
from wasl.services.errors import DomainError


def _groups(lang: np.ndarray) -> pd.DataFrame:
    n = len(lang)
    return pd.DataFrame({"language": lang, "nationality_group": ["x"] * n, "gender": ["f"] * n,
                         "age_band": ["50-59"] * n, "region": ["r"] * n})


def test_language_recall_gate_flags_a_credibly_under_flagged_group():
    """English positives scored below the top-30 % threshold while other groups' positives are flagged: the
    bootstrap CI of English recall lies wholly below overall - tolerance, so the gate fails and blocks promotion."""
    rng = np.random.default_rng(7)
    lang = np.array(["en"] * 300 + ["ar"] * 300)
    y = (rng.random(600) < 0.25).astype(int)
    p = np.where(lang == "ar", 0.2 + 0.6 * y, 0.05 + 0.1 * rng.random(600))  # English cases never reach the top
    metrics, fairness = registry.evaluate(y, p, _groups(lang))
    assert fairness["language"]["underserved_tpr"] == ["en"]
    metrics["calibration"] = {}
    gates = registry.gate_results(metrics, fairness, None)
    assert gates["fairness_recall:language"]["passed"] is False and gates["all_passed"] is False


def test_language_recall_gate_passes_when_groups_are_served_alike():
    rng = np.random.default_rng(8)
    lang = np.array(["en"] * 300 + ["ar"] * 300)
    y = (rng.random(600) < 0.25).astype(int)
    p = 0.2 + 0.5 * y + 0.05 * rng.random(600)
    metrics, fairness = registry.evaluate(y, p, _groups(lang))
    assert fairness["language"]["underserved_tpr"] == []
    metrics["calibration"] = {}
    assert registry.gate_results(metrics, fairness, None)["fairness_recall:language"]["passed"] is True


def test_gate_failing_candidate_is_never_promoted(client, as_user, db):
    """A candidate failing only the language recall gate is refused, by API and service layer, even for a clinical
    lead, and even if its status were tampered to 'awaiting_approval'. Production is unchanged."""
    mv = db.scalar(select(ModelVersion).where(ModelVersion.status != "production").order_by(ModelVersion.version.desc()))
    assert mv is not None
    prod_before = registry.production(db)
    gates = {k: ({**v, "passed": True} if isinstance(v, dict) else v) for k, v in mv.gates.items()}
    gates["fairness_recall:language"] = {"value": ["en"], "threshold": "no group recall CI wholly below overall-0.15",
                                         "passed": False, "gap": 0.38}
    gates["all_passed"] = False
    saved = (mv.status, mv.gates)
    mv.status, mv.gates = "failed_gates", gates
    db.commit()
    try:
        r = client.post(f"/api/governance/models/{mv.version}/approve", headers=as_user("fatima.al.mansoori"),
                        json={"note": "Override the language fairness gate please."})
        assert r.status_code == 409, r.text
        db.expire_all()
        mv = db.scalar(select(ModelVersion).where(ModelVersion.version == mv.version))
        mv.status = "awaiting_approval"  # tampered status: the all_passed guard must still refuse
        db.flush()
        u = db.scalar(select(User).where(User.username == "fatima.al.mansoori"))
        lead = Actor(user_id=u.id, role="clinical_lead", display=u.display_name)
        with pytest.raises(DomainError, match="did not pass all promotion gates"):
            registry.approve(db, mv.version, lead, "Reviewed and approved anyway.")
        db.rollback()
        db.expire_all()
        now_prod = registry.production(db)
        assert (now_prod.version if now_prod else None) == (prod_before.version if prod_before else None)
    finally:
        db.rollback()
        mv = db.scalar(select(ModelVersion).where(ModelVersion.version == mv.version))
        mv.status, mv.gates = saved
        db.commit()


def test_language_is_monitored_not_an_identity_input():
    assert "language" not in FEATURES and "language" in FAIRNESS_ATTRS
    assert "language_unsupported" in NUM_FEATURES and not {"gender", "nationality_group"} & set(FEATURES)
    flags = language_unsupported(pd.Series(["en", "ar", "hi", "ur", "tl", "ml"])).tolist()
    assert flags == [0, 0, 0, 0, 1, 1]


def test_case_frame_keeps_language_for_older_models_and_fairness(db, seeded):
    df = case_frame(db)
    assert {"language", "language_unsupported"} <= set(df.columns)
    assert (df.language_unsupported == (~df.language.isin(["en", "ar", "hi", "ur"])).astype(int)).all()


@pytest.mark.parametrize("feat", FEATURES)
def test_every_model_input_has_a_staff_readable_rationale(feat):
    """Every factor that can appear in 'Why this outreach priority' reads as plain words, never a column name."""
    for value in (["colorectal_fit", "urgent", "Abu Dhabi"] if feat in CAT_FEATURES else [0, 1]):
        v = {"programme": "colorectal_fit", "tier": "urgent", "region": "Abu Dhabi"}.get(feat, value)
        text = _label(feat, v)
        head = text.split(":")[0]
        assert text and "_" not in head and head != feat
    assert "interpreter" in _label("language_unsupported", 1)


def test_scored_cases_always_carry_a_rationale_list(db, seeded):
    """Every case shown with a priority has a stored rationale (possibly empty = 'close to the typical case')."""
    scored = db.scalars(select(Case).where(Case.risk_score.is_not(None))).all()
    for c in scored:
        assert isinstance(c.risk_rationale, list)
        for w in c.risk_rationale:
            assert w["text"] and w["direction"] in {"raises", "lowers"} and abs(w["effect"]) >= 0.02
