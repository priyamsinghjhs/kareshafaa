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


def _media_blocks(css: str, query: str) -> list[str]:
    """Return the body of every @media <query> { ... } block, nested-brace aware."""
    out, i, q = [], 0, f"@media {query}"
    while True:
        j = css.find(q, i)
        if j < 0:
            return out
        k = css.find("{", j)
        depth, p = 1, k + 1
        while p < len(css) and depth:
            if css[p] == "{":
                depth += 1
            elif css[p] == "}":
                depth -= 1
            p += 1
        out.append(css[k + 1:p - 1])
        i = p


def test_stacked_table_labels_sit_in_flow_above_the_value():
    """On phones, column labels (td::before from data-label) are an in-flow block above the
    cell value. They must never be position:absolute — that is what put DATE on top of
    '05 Aug 2026' on ~390 px Android Chrome after the 11 Nov token pass (the absolute label
    and the 34% left gutter drifted apart)."""
    phone = _media_blocks(CSS, "(max-width: 599px)")
    assert phone, "expected a phone breakpoint for stacked tables"
    before_rules = []
    for block in phone:
        for m in re.finditer(r"table\.compact\s+td::before\s*\{([^}]*)\}", block):
            before_rules.append(m.group(1))
    assert before_rules, "table.compact td::before must be defined at the phone breakpoint"
    for body in before_rules:
        assert "content: attr(data-label)" in body
        assert "display: block" in body
        assert "position: absolute" not in body
        assert "position: fixed" not in body
        # in-flow (static is the default; we set it so a later absolute cannot sneak in unnoticed)
        assert "position: static" in body
    # and no later / wider rule may put the stacked label back on top of the value
    for m in re.finditer(r"table\.compact\s+td::before\s*\{([^}]*)\}", CSS):
        assert "position: absolute" not in m.group(1)
        assert "position: fixed" not in m.group(1)
