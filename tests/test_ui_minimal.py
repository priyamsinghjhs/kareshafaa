"""Minimal interface (Nov 2026): less instructional copy, number-first tiles, icons with every status word, no
prototype wording on screen, and the shortened patient copy present in every patient language."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "kareshafaa"
TEMPLATES = ROOT / "templates"
JS = ROOT / "static" / "js"


def test_sign_in_is_logo_one_line_and_the_form(client):
    from kareshafaa.comms.signin_text import LOGIN_TEXT

    for lang in ("en", "ar", "hi", "ur"):
        html = client.get(f"/login?lang={lang}").text
        assert LOGIN_TEXT[lang]["tagline"] in html and 'id="login-form"' in html
        assert "Care coordinators" not in html and "carried all the way to care" not in html
        assert "prototype" not in html.lower() and "v0.1" not in html


def test_no_long_page_introductions_in_templates():
    for f in TEMPLATES.glob("*.html"):
        for m in re.finditer(r'<p class="lede"[^>]*>(.*?)</p>', f.read_text(encoding="utf-8"), re.S):
            words = re.sub(r"<[^>]+>|\{[%{].*?[%}]\}", " ", m.group(1)).split()
            assert len(words) <= 12, (f.name, m.group(1))


def test_sidebar_essay_and_prototype_footer_are_gone(client, as_user):
    html = client.get("/queue", headers=as_user("aisha.rahman")).text
    assert "sidebar-foot" not in html and "the ML score only orders outreach" not in html
    assert "not a certified medical device" in html and "Prototype" not in html and "v0.1" not in html


def test_icon_sprite_has_the_status_symbols():
    sprite = (TEMPLATES / "_icons.html").read_text(encoding="utf-8")
    for sym in ("clock", "alert", "user-x", "flag", "eye", "ok", "arrow", "bolt", "route", "gauge", "pulse"):
        assert f'id="i-{sym}"' in sprite, sym


@pytest.mark.parametrize("lang", ["en", "ar", "hi", "ur"])
def test_patient_view_has_no_prototype_wording_and_an_urgency_level(client, as_user, lang):
    d = client.get(f"/api/me/case?lang={lang}", headers=as_user(f"patient.{lang}")).json()
    assert "prototype" not in str(d).lower()
    assert ("urgency_level" in d) and (d["urgency_level"] is None or d["urgency_level"] in {"critical", "urgent", "high", "routine"})
    assert "پروٹوٹائپ" not in str(d) and "प्रोटोटाइप" not in str(d) and "نموذج أولي" not in str(d)


def test_patient_app_strings_exist_in_every_language():
    src = (JS / "patient_app.js").read_text(encoding="utf-8")
    block = src[src.index("const EXTRA = {"):src.index("};", src.index("const EXTRA = {"))]
    for lang in ("en", "ar", "hi", "ur"):
        line = next(x for x in block.splitlines() if x.strip().startswith(f"{lang}:"))
        for key in ("more:", "roles:", "team:", "coordinator:", "gp:", "specialist:", "clinical_lead:"):
            assert key in line, (lang, key)
    assert "d.label" not in src  # the English-only label line is no longer shown on translated screens
