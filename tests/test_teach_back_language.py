"""Teach-back and other patient-facing surfaces stay in one language.

The correct teach-back option for "What is your next step?" is filled from
programme_text()["next_step"]. When a translation exists it is used on display
surfaces (use_draft=True) even if translator sign-off is still pending, so an
Arabic understanding check is not mixed with an English correct answer.
Outbound gated result notifications still send signed English plus an
interpreter call until both sign-offs are in place.
"""
from __future__ import annotations

import re
from types import SimpleNamespace as NS

import pytest

from kareshafaa.comms import content as C
from kareshafaa.comms import event_text as E
from kareshafaa.comms import request_text, self_service_text, signin_text
from kareshafaa.comms.service import render_message, teach_back_form

LANGS = ("ar", "hi", "ur")
ALL_LANGS = ("en", *LANGS)
PROGRAMMES = tuple(C.PROGRAMME_TEXT["en"])
PATHWAYS = (None, *C.PATHWAY_TEXT_EN, "colonoscopy", "colposcopy", "reflex_cytology", "reflex_hpv",
            "assessment_imaging", "biopsy", "human_review")
RULES = (
    (True, None), (False, None), (False, "CX-HPV1618"), (False, "BR-4A"), (False, "BR-5B"),
    (False, "DM-CRIT"), (False, "X"), (True, "CX-HIGH-GRADE"), (True, "CX-SUSP-INVASIVE"),
    (True, "CX-HPVOTHER-LG"), (True, "CX-UNSAT"), (True, "CRC-FIT-POS"), (True, "BR-0"),
)
TIERS = ("critical", "urgent", "high", "routine")

# Latin tokens that may appear in a non-English patient-facing string on purpose.
# Prefer a translated term with the English in parentheses when a new name is added.
_ALLOW = {
    "hpv", "fit", "hba1c", "hba", "a1c", "bi-rads", "birads", "bi", "rads",
    "kareshafaa", "uae", "sms", "id", "pin", "otp",
    "mmol", "mol", "mg", "dl", "mm", "hg", "mmhg", "cm", "ml", "kg", "ug", "g",
    "mumol", "dna", "cin", "whatsapp",
    "https", "http", "www", "com", "org", "example",
    "dr", "xxx", "xxxx", "rtl", "ltr", "utc", "gmt", "gp",
    "tagalog", "malayalam",  # interpreter-notice language names
    "asia", "dubai",
}

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+./%-]*")
_PLACE = re.compile(r"\{[^}]+\}")
_URL = re.compile(r"https?://\S+")
_EMAIL = re.compile(r"[A-Z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", re.I)


def latin_leaks(text: str, lang: str) -> list[str]:
    """Latin-script words in a non-English string that are not on the allow-list."""
    if lang == "en" or not text:
        return []
    text = _URL.sub(" ", text)
    text = _EMAIL.sub(" ", text)
    text = _PLACE.sub(" ", text)
    found = []
    for m in _TOKEN.finditer(text):
        tok = m.group()
        key = tok.lower().rstrip(".")
        # Split compound units (mmol/mol, mg/dL, µg/g) so each part can be allow-listed.
        parts = [p for p in re.split(r"[/.]", key) if p]
        if parts and all(len(p) <= 1 or p in _ALLOW for p in parts):
            continue
        found.append(tok)
    return found


def _case(*, programme="cervical", needs_confirmation=True, pathway="colposcopy_urgent",
          rule_id="CX-HIGH-GRADE", tier="urgent"):
    return NS(programme=programme, needs_confirmation=needs_confirmation, pathway=pathway,
              rule_id=rule_id, tier=tier, id=7, patient=NS(given_name="Sara", mrn="M7", language="ar"))


def _branches():
    for prog in PROGRAMMES:
        for pw in PATHWAYS:
            for nc, rid in RULES:
                for tier in TIERS:
                    yield _case(programme=prog, needs_confirmation=nc, pathway=pw, rule_id=rid, tier=tier)


# ---------------------------------------------------------------- root cause


def test_arabic_colposcopy_urgent_correct_option_is_arabic():
    """The screenshot bug: option b (the correct next step) was the English pathway text."""
    form = teach_back_form(_case(), "ar")
    q2 = next(q for q in form["questions"] if q["id"] == "q2")
    assert q2["text"] == "ما هي خطوتك التالية؟"
    correct = next(o["text"] for o in q2["options"] if o["key"] == C.TEACH_BACK_CORRECT["q2"])
    expected = C.PATHWAY_TEXT["ar"]["colposcopy_urgent"]["next_step"]
    assert correct == expected
    assert "colposcopy" not in correct.lower()
    assert "closer look" not in correct.lower()
    assert "فحص عنق الرحم بالمنظار" in correct
    assert "تنظير المهبل/عنق الرحم" in correct
    assert "خلال أسبوعين" in correct
    assert not latin_leaks(correct, "ar")


@pytest.mark.parametrize("lang,needle", [
    ("ar", "فحص عنق الرحم بالمنظار (تنظير المهبل/عنق الرحم) في عيادة المستشفى، خلال أسبوعين"),
    ("hi", "अस्पताल क्लिनिक में गर्भाशय ग्रीवा की नज़दीकी जांच (कोल्पोस्कोपी), 2 हफ़्तों के अंदर"),
    ("ur", "ہسپتال کے کلینک میں گردنِ رحم کا قریبی معائنہ (کولپوسکوپی)، 2 ہفتوں کے اندر"),
])
def test_colposcopy_urgent_option_translated(lang, needle):
    form = teach_back_form(_case(), lang)
    q2 = next(q for q in form["questions"] if q["id"] == "q2")
    correct = next(o["text"] for o in q2["options"] if o["key"] == C.TEACH_BACK_CORRECT["q2"])
    assert correct == needle
    assert correct == C.PATHWAY_TEXT[lang]["colposcopy_urgent"]["next_step"]
    assert not latin_leaks(correct, lang)


def test_draft_is_used_when_only_english_is_signed_off():
    """Production seed: English pathway wording is live, translations are not."""
    live = {f"pathway:{pw}:en" for pw in C.PATHWAY_TEXT_EN}
    case = _case()
    shown = C.programme_text("ar", case, live, use_draft=True)
    gated = C.programme_text("ar", case, live)
    assert shown["next_step"] == C.PATHWAY_TEXT["ar"]["colposcopy_urgent"]["next_step"]
    assert shown["translation_pending"] == "true"
    assert shown.get("translation_fallback") is None
    assert gated["next_step"] == C.PATHWAY_TEXT_EN["colposcopy_urgent"]["next_step"]
    assert gated["translation_fallback"] == "true"
    assert gated["translation_pending"] == "true"


def test_outbound_message_still_uses_english_when_translation_is_pending():
    """The gated send path (use_draft=False) still returns signed English, not the draft."""
    live = {f"pathway:{pw}:en" for pw in C.PATHWAY_TEXT_EN}
    case = _case(programme="diabetes_hba1c", pathway="prediabetes_gp", rule_id="DM-PRE", tier="routine")
    gated = C.programme_text("ar", case, live)
    assert gated["translation_pending"] == "true" and gated["translation_fallback"] == "true"
    assert gated["meaning"] == C.PATHWAY_TEXT_EN["prediabetes_gp"]["meaning"]
    assert gated["next_step"] == C.PATHWAY_TEXT_EN["prediabetes_gp"]["next_step"]
    shown = C.programme_text("ar", case, live, use_draft=True)
    assert shown["meaning"] == C.PATHWAY_TEXT["ar"]["prediabetes_gp"]["meaning"]


def test_outbound_render_switches_to_english_and_flags_interpreter(monkeypatch):
    from kareshafaa.comms import governance as G

    live = {f"pathway:{pw}:en" for pw in C.PATHWAY_TEXT_EN}
    monkeypatch.setattr(G, "live_content", lambda case: live)
    case = _case(programme="diabetes_hba1c", pathway="prediabetes_gp", rule_id="DM-PRE", tier="routine")
    body, sent_lang, interp = render_message("result_notification", "ar", case, variant="standard")
    assert sent_lang == "en" and interp is True
    assert C.PATHWAY_TEXT_EN["prediabetes_gp"]["meaning"] in body
    assert C.PATHWAY_TEXT["ar"]["prediabetes_gp"]["meaning"] not in body


# ---------------------------------------------------------------- systematic teach-back audit


def test_every_teach_back_form_stays_in_one_language():
    bad = []
    n = 0
    for lang in ALL_LANGS:
        for case in _branches():
            form = teach_back_form(case, lang)
            n += 1
            texts = [form["title"], form["intro"], form["confidence"]]
            texts += list(form["labels"].values())
            for q in form["questions"]:
                texts.append(q["text"])
                texts += [o["text"] for o in q["options"]]
            for text in texts:
                leaks = latin_leaks(text, lang)
                if leaks:
                    bad.append((lang, case.programme, case.pathway, case.rule_id, case.tier,
                                leaks, text[:160]))
    assert n == len(PROGRAMMES) * len(PATHWAYS) * len(RULES) * len(TIERS) * len(ALL_LANGS)
    assert n > 2000
    assert not bad, bad[:8]


def test_every_display_programme_text_stays_in_one_language():
    bad = []
    live_en = {f"pathway:{pw}:en" for pw in C.PATHWAY_TEXT_EN}
    live_all = live_en | {f"pathway:{pw}:{lang}" for pw in C.PATHWAY_TEXT_EN for lang in LANGS}
    for lang in LANGS:
        for case in _branches():
            for live in (set(), live_en, live_all):
                t = C.programme_text(lang, case, live, use_draft=True)
                for field in ("name", "meaning", "next_step"):
                    leaks = latin_leaks(t[field], lang)
                    if leaks:
                        bad.append((lang, field, case.programme, case.pathway, case.rule_id,
                                    leaks, t[field][:160]))
    assert not bad, bad[:8]


def test_event_mail_wording_stays_in_one_language():
    """Rendered appointment and result email *wording* (not proper nouns) has no English prose."""
    from kareshafaa.comms.event_mail import _meaning_and_next, render_appointment, render_result
    from kareshafaa.comms.event_text import SERVICE_AND_TEST_NAMES, TEXT, clinical_name

    bad = []
    for lang in LANGS:
        for name, table in (("TEXT", TEXT[lang]), ("SERVICE", SERVICE_AND_TEST_NAMES[lang]),
                            ("NEGATIVE", E.NEGATIVE_MEANING[lang]), ("RANGE", E.RANGE[lang]),
                            ("ROLES", E.ROLES[lang]), ("SPECIALTIES", E.SPECIALTIES[lang]),
                            ("DATE_FMT", {"d": E.DATE_FMT[lang]}),
                            ("WEEKDAYS", dict(enumerate(E.WEEKDAYS[lang]))),
                            ("MONTHS", dict(enumerate(E.MONTHS[lang])))):
            def walk(obj, acc=None):
                if acc is None:
                    acc = []
                if isinstance(obj, dict):
                    for v in obj.values():
                        walk(v, acc)
                elif isinstance(obj, str):
                    acc.extend(latin_leaks(obj, lang))
                return acc

            leaks = walk(table)
            if leaks:
                bad.append((lang, name, leaks))
        # programme-level meaning/next_step used when the pathway translation is not live
        for prog in PROGRAMMES:
            case = _case(programme=prog, pathway="colposcopy_urgent" if prog == "cervical" else
                         "repeat_fit" if prog == "colorectal_fit" else
                         "prediabetes_gp" if prog == "diabetes_hba1c" else None,
                         needs_confirmation=True, rule_id="CX-HIGH-GRADE")
            live = {f"pathway:{pw}:en" for pw in C.PATHWAY_TEXT_EN}
            # _meaning_and_next uses programme_text default (gated) then PROGRAMME_TEXT on fallback
            ctx = {"case": case, "programme": prog, "report": None, "obs": None, "conclusion": "actionable"}
            # Bind live by going through programme_text with the explicit set via a wrapper: the
            # helper calls programme_text(lang, case) which uses live_content (empty). Drive the
            # same fallback by setting translation_fallback ourselves is unnecessary — with an
            # unbound case, live is empty, programme_text returns NEUTRAL English + pending, and
            # _meaning_and_next should then use PROGRAMME_TEXT[lang].
            meaning, next_step = _meaning_and_next(lang, ctx)
            for field, val in (("meaning", meaning), ("next_step", next_step)):
                leaks = latin_leaks(val, lang)
                if leaks:
                    bad.append((lang, f"email-{field}", prog, leaks, val[:160]))
        for en_name, tr in SERVICE_AND_TEST_NAMES[lang].items():
            leaks = latin_leaks(tr, lang)
            if leaks:
                bad.append((lang, "clinical_name", en_name, leaks, tr))
            # The English source itself must not appear untranslated (except allow-listed terms
            # such as HPV that we keep in parentheses).
            if en_name[0].isupper() and " " in en_name:
                # multi-word English catalogue names should not survive as a whole
                if en_name in tr and en_name.lower() not in _ALLOW:
                    # if the whole English string is the translation, that's a leak
                    if tr == en_name:
                        bad.append((lang, "untranslated", en_name))
    assert not bad, bad[:8]


def test_static_patient_facing_tables_have_no_english_prose():
    """Catch English source strings left in translation tables (placeholders and allow-listed terms excepted)."""
    # TRANSLATION_STATUS is a staff-facing English draft flag, not shown to patients.
    skip = {"TRANSLATION_STATUS", "ALARM_WORDS"}
    bad = []
    for label, mod in (("content", C), ("event_text", E), ("request_text", request_text),
                       ("self_service_text", self_service_text), ("signin_text", signin_text)):
        for name, val in vars(mod).items():
            if name in skip or not name.isupper() or not isinstance(val, dict):
                continue
            def walk(obj, lang=None, path=""):
                hits = []
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        nlang = k if k in ALL_LANGS or k in ("tl", "ml") else lang
                        hits.extend(walk(v, nlang, f"{path}.{k}"))
                elif isinstance(obj, (list, tuple)):
                    for i, v in enumerate(obj):
                        hits.extend(walk(v, lang, f"{path}[{i}]"))
                elif isinstance(obj, str) and lang and lang not in ("en", "tl", "ml"):
                    leaks = latin_leaks(obj, lang)
                    if leaks:
                        hits.append((label, name, path, lang, leaks, obj[:160]))
                return hits
            bad.extend(walk(val, path=f"{label}.{name}"))
    assert not bad, bad[:8]


# ---------------------------------------------------------------- live database path (English signed off)


def test_teach_back_form_on_a_live_colposcopy_urgent_case(db, make_patient):
    """Seed signs off English pathway wording only. The Arabic form must still use the draft."""
    import random

    from kareshafaa.comms import governance as G
    from kareshafaa.models import Patient, utcnow
    from kareshafaa.services import workflow as wf

    live = G.live_ids(db)
    assert "pathway:colposcopy_urgent:en" in live
    assert "pathway:colposcopy_urgent:ar" not in live

    now = utcnow()
    p = Patient(mrn=f"TB-CX-{random.randint(0, 10**9)}", given_name="Shamma", family_name="Test",
                gender="female", birth_year=1980, age_band="40-49", nationality_group="emirati",
                language="ar", region="Abu Dhabi", phone_valid=True, has_smartphone=True,
                whatsapp=False, managing_org_id=3, gp_id=19, responsiveness=0.8)
    db.add(p)
    db.flush()
    _, case = wf.ingest_result(db, p, "cervical", value=None, value_text="HPV16", cytology="HSIL",
                               performer_org_id=1, now=now)
    assert case is not None and case.pathway == "colposcopy_urgent"
    form = teach_back_form(case, "ar")
    q2 = next(q for q in form["questions"] if q["id"] == "q2")
    correct = next(o["text"] for o in q2["options"] if o["key"] == "b")
    assert correct == C.PATHWAY_TEXT["ar"]["colposcopy_urgent"]["next_step"]
    assert not latin_leaks(correct, "ar")
    # Hindi and Urdu on the same case
    for lang in ("hi", "ur"):
        form = teach_back_form(case, lang)
        q2 = next(q for q in form["questions"] if q["id"] == "q2")
        correct = next(o["text"] for o in q2["options"] if o["key"] == "b")
        assert correct == C.PATHWAY_TEXT[lang]["colposcopy_urgent"]["next_step"]
        assert not latin_leaks(correct, lang)
    # English still uses the signed-off wording
    form = teach_back_form(case, "en")
    q2 = next(q for q in form["questions"] if q["id"] == "q2")
    correct = next(o["text"] for o in q2["options"] if o["key"] == "b")
    assert correct == C.PATHWAY_TEXT_EN["colposcopy_urgent"]["next_step"]
    db.rollback()


def test_staff_and_patient_app_paths_use_the_arabic_draft(db, client, as_user):
    """The staff teach-back form and the wording the patient app uses share the Arabic draft."""
    import random

    from kareshafaa.models import Patient, utcnow
    from kareshafaa.services import workflow as wf
    from kareshafaa.statemachine import S

    now = utcnow()
    p = Patient(mrn=f"TB-APP-{random.randint(0, 10**9)}", given_name="Fatima", family_name="Test",
                gender="female", birth_year=1980, age_band="40-49", nationality_group="emirati",
                language="ar", region="Abu Dhabi", phone_valid=True, has_smartphone=True,
                whatsapp=False, managing_org_id=3, gp_id=19, responsiveness=0.8)
    db.add(p)
    db.flush()
    _, case = wf.ingest_result(db, p, "cervical", value=None, value_text="HPV16", cytology="HSIL",
                               performer_org_id=1, now=now)
    assert case is not None and case.pathway == "colposcopy_urgent"
    case.state = S.PATIENT_NOTIFIED
    db.commit()
    r = client.get(f"/api/cases/{case.id}/teach-back-form?lang=ar", headers=as_user("aisha.rahman"))
    assert r.status_code == 200, r.text
    form = r.json()
    q2 = next(q for q in form["questions"] if q["id"] == "q2")
    correct = next(o["text"] for o in q2["options"] if o["key"] == "b")
    expected = C.PATHWAY_TEXT["ar"]["colposcopy_urgent"]["next_step"]
    assert correct == expected
    # Same wording the patient app would show (me.py calls programme_text(..., use_draft=True)).
    shown = C.programme_text("ar", case, use_draft=True)
    assert shown["next_step"] == expected
    assert shown["meaning"] == C.PATHWAY_TEXT["ar"]["colposcopy_urgent"]["meaning"]
    assert shown.get("translation_fallback") is None
    assert shown["translation_pending"] == "true"
    db.rollback()


def test_seeded_arabic_patient_app_has_no_english_prose(client, as_user):
    """Whatever case patient.ar currently has, the selected-language fields must not mix in English."""
    d = client.get("/api/me/case?lang=ar", headers=as_user("patient.ar")).json()
    assert d["language"] == "ar"
    for field in ("programme", "meaning", "next_step", "timeframe", "urgency"):
        if d.get(field):
            leaks = latin_leaks(d[field], "ar")
            assert not leaks, (field, leaks, d[field])
    if d.get("teach_back") and d["teach_back"].get("questions"):
        for q in d["teach_back"]["questions"]:
            assert not latin_leaks(q["text"], "ar"), q["text"]
            for o in q["options"]:
                assert not latin_leaks(o["text"], "ar"), o["text"]
    # The interpreter notice is only for languages with no translation at all (e.g. Tagalog).
    if d.get("interpreter_notice"):
        assert "Tagalog" in d["interpreter_notice"] or "Malayalam" in d["interpreter_notice"]
