"""Fix 5: strict script CSP. No inline <script> blocks and no on* event-handler attributes anywhere, so
script-src is 'self' only (no 'unsafe-inline'). Page data travels in non-executable JSON blocks."""
import json
import re
from pathlib import Path

import pytest

from kareshafaa.main import CSP

PKG = Path(__file__).resolve().parents[1] / "kareshafaa"
INLINE_SCRIPT = re.compile(r"<script\b(?![^>]*\bsrc=)(?![^>]*type=\"application/json\")[^>]*>", re.I)
HANDLER = re.compile(r"""\son[a-z]+\s*=\s*["'\\]""", re.I)


def _directive(name):
    for part in CSP.split(";"):
        bits = part.split()
        if bits and bits[0] == name:
            return bits[1:]
    raise AssertionError(name)


def test_script_src_is_self_only():
    assert _directive("script-src") == ["'self'"]
    assert _directive("script-src-attr") == ["'none'"]
    assert "'unsafe-eval'" not in CSP
    assert "'unsafe-inline'" in _directive("style-src")  # documented exception (style="" attributes)


@pytest.mark.parametrize("path", sorted((PKG / "templates").glob("*.html")), ids=lambda p: p.name)
def test_templates_have_no_inline_scripts_or_handlers(path):
    s = path.read_text()
    assert not INLINE_SCRIPT.search(s), INLINE_SCRIPT.search(s).group(0)
    assert not HANDLER.search(s), HANDLER.search(s).group(0)
    assert "javascript:" not in s.lower()


@pytest.mark.parametrize("path", sorted((PKG / "static").rglob("*.js")), ids=lambda p: p.name)
def test_static_js_builds_no_inline_handlers(path):
    s = path.read_text()
    assert not HANDLER.search(s), HANDLER.search(s).group(0)
    assert "<script" not in s.lower()


def _executable_inline(html):
    return [m.group(0) for m in INLINE_SCRIPT.finditer(html)] + [m.group(0) for m in HANDLER.finditer(html)]


@pytest.mark.parametrize("user,path", [
    (None, "/login"), (None, "/login?lang=ar"), (None, "/forgot-username"), (None, "/forgot-password?lang=ar"), (None, "/activate"), (None, "/reset-password?lang=ar"), (None, "/offline"), (None, "/starting"),
    ("aisha.rahman", "/queue"), ("aisha.rahman", "/patients"), ("aisha.rahman", "/cases/1"),
    ("omar.haddad", "/queue?view=review"), ("fatima.al.mansoori", "/overview"), ("fatima.al.mansoori", "/governance"),
    ("admin", "/admin/users"), ("admin", "/admin/system"), ("admin", "/walkthrough"), ("admin", "/audit"),
    ("patient.en", "/patient"), ("patient.ar", "/patient")])
def test_rendered_pages_have_no_inline_script(client, as_user, user, path):
    r = client.get(path, headers=as_user(user) if user else {})
    assert r.status_code == 200, (path, r.status_code)
    assert r.headers["content-security-policy"] == CSP
    assert not _executable_inline(r.text), (path, _executable_inline(r.text)[:3])
    for block in re.findall(r'<script type="application/json" id="[^"]+">(.*?)</script>', r.text, re.S):
        json.loads(block)  # data blocks are valid JSON (and never executed)
        assert "</" not in block


def test_auth_strings_travel_in_a_data_block(client):
    html = client.get("/login?lang=ar").text
    data = json.loads(re.search(r'id="page-strings">(.*?)</script>', html, re.S).group(1))
    assert set(data) == {"show", "hide", "missing"} and all(data.values())
    assert "js/auth_forms.js" in html
