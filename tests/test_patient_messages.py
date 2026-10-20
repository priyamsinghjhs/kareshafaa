"""Patient messages: reading level + tone in every language, translations of the pathway texts, and the
human sign-off gate (clinical + professional translator, bound to the exact wording)."""
import re

import pytest
from sqlalchemy import delete

from wasl.comms import content as C
from wasl.comms import governance as G
from wasl.comms.readability import measure, sentences, words
from wasl.models import ContentApproval
from wasl.security import Actor
from wasl.services.errors import DomainError, Forbidden

LANGS = ("ar", "hi", "ur")


def test_every_patient_facing_text_passes_reading_level_and_tone():
    bad = {}
    for cid, it in G.catalogue().items():
        r = measure(it["measured"], it["lang"], **it["opts"])
        if not r.ok:
            bad[cid] = r.problems
    assert not bad, bad


def test_english_grade_levels():
    for cid, it in G.catalogue().items():
        if it["lang"] == "en":
            r = measure(it["measured"], "en", **it["opts"])
            if r.words >= 12:
                assert r.fk_grade <= 8.0 and r.reading_ease >= 60, (cid, r.fk_grade, r.reading_ease)


@pytest.mark.parametrize("text,problem", [
    ("Following histopathological evaluation, the multidisciplinary team determined that additional colposcopic "
     "investigation is clinically indicated notwithstanding the preliminary cytological classification.", "grade"),
    ("You failed to attend your appointment. You must rebook.", "blaming"),
    ("Your test may show cancer.", "alarming"),
    ("Please CALL us NOW about your result.", "shouting"),
    ("Your result is ready!", "exclamation"),
])
def test_checks_catch_bad_english(text, problem):
    r = measure(text, "en")
    assert not r.ok and any(problem in p.lower() for p in r.problems), r.problems


def test_proxy_checks_catch_long_sentences_in_other_languages():
    long_hi = " ".join(["जांच"] * 35) + "।"
    assert any("longest sentence" in p for p in measure(long_hi, "hi").problems)
    assert any("alarming" in p for p in measure("قد يكون لديك سرطان.", "ar").problems)
    assert measure("هذا لا يعني أنك مصابة بالسرطان.", "ar").ok


def test_tokenisers_handle_scripts():
    assert words("आपकी जांच में खून मिला है।") == ["आपकी", "जांच", "में", "खून", "मिला", "है"]
    assert len(sentences("پہلا جملہ۔ دوسرا جملہ۔", "ur")) == 2
    assert len(sentences("أول جملة. هل فهمت؟ نعم", "ar")) == 3
    assert len(sentences("Hello {name}. Open {link}.", "en")) == 2


def test_pathway_translations_complete_and_flagged():
    for lang in LANGS:
        assert set(C.PATHWAY_TEXT[lang]) == set(C.PATHWAY_TEXT_EN)
        assert "translator review" in C.TRANSLATION_STATUS[lang]
        for pw, en in C.PATHWAY_TEXT_EN.items():
            tr = C.PATHWAY_TEXT[lang][pw]
            assert set(tr) == set(en)
            for k in en:  # numbers and test names must survive translation
                for tok in re.findall(r"\b(?:\d+|HPV)\b", en[k]):
                    if lang == "ar" and tok == "2" and "أسبوعين" in tr[k]:
                        continue  # Arabic dual form = "two weeks"
                    assert tok in tr[k], (lang, pw, k, tok)
                assert tr[k] != en[k]


def test_unreviewed_translations_are_not_used(db):
    live = G.live_ids(db)
    for pw in C.PATHWAY_TEXT_EN:
        assert f"pathway:{pw}:en" in live  # demo seed: clinical lead signed off the English wording
        for lang in LANGS:
            assert f"pathway:{pw}:{lang}" not in live

    class Case:
        programme, needs_confirmation, pathway = "colorectal_fit", True, "repeat_fit"
    t = C.programme_text("ar", Case, live)
    assert t["meaning"] == C.PATHWAY_TEXT_EN["repeat_fit"]["meaning"] and t["translation_pending"] == "true"
    rows = {r["content_id"]: r for r in G.status(db)}
    assert rows["pathway:repeat_fit:ar"]["translation_status"].startswith("draft")
    assert not rows["pathway:repeat_fit:ar"]["live"]


def _actors(db):
    from wasl.models import User
    from sqlalchemy import select
    lead = db.scalar(select(User).where(User.role == "clinical_lead"))
    tr = db.scalar(select(User).where(User.role == "translator"))
    return (Actor(user_id=lead.id, role="clinical_lead", display=lead.display_name),
            Actor(user_id=tr.id, role="translator", display=tr.display_name))


def test_both_signoffs_make_translation_live_and_edits_invalidate(db, monkeypatch):
    lead, tr = _actors(db)
    cid = "pathway:surveillance_1y:hi"
    try:
        with pytest.raises(Forbidden):
            G.approve(db, tr, cid, "clinical", "translator cannot sign clinically")
        with pytest.raises(Forbidden):
            G.approve(db, lead, cid, "translation", "lead is not a translator")
        G.approve(db, tr, cid, "translation", "Checked against the English source; meaning preserved.")
        assert cid not in G.live_ids(db)  # translation alone is not enough
        with pytest.raises(DomainError):
            G.approve(db, tr, cid, "translation", "duplicate sign-off")
        # separation of duties: same person (e.g. after a role change) cannot add the other sign-off
        same = Actor(user_id=tr.user_id, role="clinical_lead", display="same person")
        with pytest.raises(DomainError, match="separation of duties"):
            G.approve(db, same, cid, "clinical", "same person second key")
        G.approve(db, lead, cid, "clinical", "Clinically correct and kind.")
        live = G.live_ids(db)
        assert cid in live

        class Case:
            programme, needs_confirmation, pathway = "cervical", True, "surveillance_1y"
        assert C.programme_text("hi", Case, live)["meaning"] == C.PATHWAY_TEXT["hi"]["surveillance_1y"]["meaning"]
        # any edit to the wording invalidates the sign-off (bound to sha256)
        edited = {**C.PATHWAY_TEXT["hi"], "surveillance_1y": {**C.PATHWAY_TEXT["hi"]["surveillance_1y"],
                                                              "meaning": "बदला हुआ पाठ। यह जांच सामान्य है।"}}
        monkeypatch.setitem(C.PATHWAY_TEXT, "hi", edited)
        monkeypatch.setattr(G, "_CAT", None)
        assert cid not in G.live_ids(db)
    finally:
        db.rollback()
        db.execute(delete(ContentApproval).where(ContentApproval.content_id == cid))
        db.commit()
        G._CAT = None


def test_english_edit_falls_back_to_neutral_message(db, monkeypatch):
    edited = {**C.PATHWAY_TEXT["en"], "repeat_fit": {**C.PATHWAY_TEXT_EN["repeat_fit"], "next_step": "a new kit"}}
    monkeypatch.setitem(C.PATHWAY_TEXT, "en", edited)
    monkeypatch.setattr(C, "PATHWAY_TEXT_EN", edited)
    monkeypatch.setattr(G, "_CAT", None)
    live = G.live_ids(db)
    assert "pathway:repeat_fit:en" not in live

    class Case:
        programme, needs_confirmation, pathway = "colorectal_fit", True, "repeat_fit"
    t = C.programme_text("en", Case, live)
    assert t["meaning"] == C.NEUTRAL_PATHWAY_TEXT["meaning"] and t["content_pending_approval"] == "true"
    monkeypatch.setattr(G, "_CAT", None)


def test_clinical_signoff_refused_when_readability_fails(db, monkeypatch):
    lead, _ = _actors(db)
    hard = "Following histopathological evaluation the multidisciplinary team determined additional colposcopic " \
           "investigation is indicated notwithstanding preliminary cytological classification."
    edited = {**C.PATHWAY_TEXT["en"], "colposcopy_urgent": {"meaning": hard, "next_step": "colposcopy"}}
    monkeypatch.setitem(C.PATHWAY_TEXT, "en", edited)
    monkeypatch.setattr(G, "_CAT", None)
    with pytest.raises(DomainError, match="reading-level"):
        G.approve(db, lead, "pathway:colposcopy_urgent:en", "clinical", "looks fine")
    db.rollback()
    monkeypatch.setattr(G, "_CAT", None)


def test_signoff_requires_named_human(db):
    with pytest.raises(Forbidden):
        G.approve(db, Actor(user_id=None, role="clinical_lead", display="bot", is_human=False),
                  "pathway:repeat_fit:en", "clinical", "automated")


def test_content_api_permissions(client, as_user):
    assert client.get("/api/governance/content", headers=as_user("admin")).status_code == 403
    r = client.get("/api/governance/content", headers=as_user("translator"))
    assert r.status_code == 200
    s = r.json()["summary"]
    assert s["items"] == s["readability_ok"] and s["gated"] == 20 and s["gated_live"] == 5
    r = client.post("/api/governance/content/signoff", headers=as_user("admin"),
                    json={"content_id": "pathway:repeat_fit:ar", "kind": "translation", "note": "admin tries"})
    assert r.status_code == 403
    r = client.post("/api/governance/content/signoff", headers=as_user("translator"),
                    json={"content_id": "pathway:repeat_fit:ar", "kind": "clinical", "note": "wrong kind for role"})
    assert r.status_code == 403
    r = client.post("/api/governance/content/signoff", headers=as_user("translator"),
                    json={"content_id": "pathway:repeat_fit:ar", "kind": "bogus", "note": "x"})
    assert r.status_code == 422
