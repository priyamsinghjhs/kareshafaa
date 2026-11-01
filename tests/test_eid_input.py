"""Emirates ID typing on phones: the form helpers in patients.js (run in node) format, place the caret and give the
same reasons as the server; every HTML pattern attribute compiles with the 'v' flag Chrome uses (an invalid pattern
throws in reportValidity and blocks the form)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from kareshafaa.services import patients as svc
from kareshafaa.services.errors import Invalid

ROOT = Path(__file__).resolve().parents[1] / "kareshafaa"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def run_pf(expr: str):
    src = (ROOT / "static/js/patients.js").read_text()
    js = ("const src = require('fs').readFileSync(process.argv[1], 'utf8');"
          "const PF = new Function('window', 'document', 'W', src + '; return PF;')({}, {addEventListener() {}}, {});"
          f"console.log(JSON.stringify(({expr})));")
    out = subprocess.run([NODE, "-e", js, str(ROOT / "static/js/patients.js")], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert src
    return json.loads(out.stdout)


def test_format_groups_and_caps_at_15_digits():
    got = run_pf("['7', '784', '7841', '7841985', '78419851', '78419851234567', '784198512345673', '7841985123456739', "
                 "' 784-1985 1234567-3 ', '784-91985-1234567'].map(PF.eidFormat)")
    assert got == ["7", "784", "784-1", "784-1985", "784-1985-1", "784-1985-1234567", "784-1985-1234567-3",
                   "784-1985-1234567-3", "784-1985-1234567-3", "784-9198-5123456-7"]


def test_caret_follows_digits_not_dashes():
    # caret after the n-th digit in the dashed value
    assert run_pf("[0, 3, 4, 7, 8, 14, 15].map(n => PF.eidCaret('784-1985-1234567-3', n))") == [0, 3, 5, 8, 10, 16, 18]


def test_reasons_match_the_server():
    cases = ["784-1985-1234567-1", "784-1985-123456", "785-1985-1234567-3", "784-1885-1234567-0", "784-2999-1234567-0", "784-1985-1234567-3", ""]
    js = run_pf(f"{json.dumps(cases)}.map(PF.eidWhy)")
    for raw, msg in zip(cases, js):
        try:
            svc.normalise_emirates_id(raw)
            server = ""
        except Invalid as e:
            server = str(e)
        assert msg.rstrip(".") == server.rstrip("."), (raw, msg, server)
    assert run_pf("PF.eidOk('784198512345673') && !PF.eidOk('784198512345671')") is True


def test_every_pattern_attribute_compiles_with_the_v_flag():
    pats = run_pf("Object.values(PF.ID_UI).map(u => (/pattern=\"([^\"]*)\"/.exec(u.attrs) || [])[1]).filter(Boolean)")
    for f in list((ROOT / "templates").glob("*.html")):
        pats += [p for p in re.findall(r'\spattern="([^"$]*)"', f.read_text())]
    assert "[0-9\\-]*" in pats
    js = f"{json.dumps(pats)}.map(p => {{ try {{ new RegExp('^(?:' + p + ')$', 'v'); return null; }} catch (e) {{ return p + ': ' + e.message; }} }}).filter(Boolean)"
    assert run_pf(js) == []


def test_placeholder_is_a_valid_number():
    ph = run_pf("/placeholder=\"([^\"]*)\"/.exec(PF.ID_UI.emirates_id.attrs)[1]")
    assert svc.normalise_emirates_id(ph) == ph
