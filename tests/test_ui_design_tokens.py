"""Design system guard (Nov 11 2026 design pass): one font stack, six type sizes, three weights, no one-off inline
styles, and the cut helper copy stays cut in every language."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "kareshafaa"
CSS = (ROOT / "static" / "kareshafaa.css").read_text(encoding="utf-8")


def test_every_font_size_and_weight_is_a_token():
    sizes = set(re.findall(r"font-size:\s*([^;}]+)", CSS))
    assert sizes <= {f"var(--fs-{k})" for k in ("xs", "sm", "base", "lg", "xl", "2xl")} | {"inherit"}, sizes
    weights = set(re.findall(r"font-weight:\s*([^;}]+)", CSS))
    assert weights <= {"var(--fw-regular)", "var(--fw-semibold)", "var(--fw-bold)"}, weights
    root = CSS[CSS.index(":root"):CSS.index("}", CSS.index(":root"))]
    assert len(re.findall(r"--fs-[a-z0-9]+:", root)) == 6 and "clamp(" not in root


def test_one_text_font_stack():
    # the text stack plus the code stack; Arabic / Urdu and Devanagari only switch script
    fams = set(re.findall(r"font-family:\s*([^;}]+)", CSS))
    assert {"var(--font)", "var(--mono)"} <= fams
    assert not any("ui-monospace" in f for f in fams), fams
    assert "@import" not in CSS and "fonts.googleapis" not in CSS


def test_no_one_off_inline_styles():
    allowed = re.compile(r'style="(width:\$\{[^"]*\}%|display:none)"')
    for f in list((ROOT / "templates").glob("*.html")) + list((ROOT / "static" / "js").glob("*.js")):
        for m in re.finditer(r'style="[^"]*"', f.read_text(encoding="utf-8")):
            assert allowed.fullmatch(m.group(0)), (f.name, m.group(0))


def test_cut_helper_copy_is_gone_in_every_language(client):
    from kareshafaa.comms.request_text import REQ_TEXT
    from kareshafaa.comms.self_service_text import LOOP_TEXT
    from kareshafaa.comms.signin_text import LOGIN_TEXT

    for lang in ("en", "ar", "hi", "ur"):
        assert "idle_note" not in LOGIN_TEXT[lang] and "request_intro" not in REQ_TEXT[lang]
        assert "consent_intro" not in LOOP_TEXT[lang] and "consent_title" in LOOP_TEXT[lang]
        html = client.get(f"/login?lang={lang}").text
        assert 'id="login-form"' in html and "Sessions end after" not in html
    tour = (ROOT / "static" / "js" / "tour.js").read_text(encoding="utf-8")
    assert "!localStorage.getItem" not in tour  # the page guide is never inserted above the work by itself
