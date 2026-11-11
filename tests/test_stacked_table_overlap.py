"""Playwright check: stacked-table labels on a phone-width viewport sit above (not on top of)
cell values (the 12 Nov 2026 overlap bug). Skipped when Chromium is not installed."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "kareshafaa" / "static" / "kareshafaa.css").read_text(encoding="utf-8")
CHROME = next((p for p in ("/usr/bin/google-chrome", "/usr/bin/chromium-browser", "/usr/bin/chromium")
               if Path(p).exists()), shutil.which("google-chrome") or shutil.which("chromium"))
try:
    import playwright  # noqa: F401
    _HAS_PW = True
except ImportError:
    _HAS_PW = False

pytestmark = pytest.mark.skipif(not _HAS_PW or not CHROME, reason="browsers absent")

FIXTURE = """<!doctype html><html lang="{lang}" dir="{dir}"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>{css}</style></head>
<body style="margin:0;padding:12px;background:#f4f6f8">
<div class="table-scroll" tabindex="0" role="region" aria-label="Table">
<table class="compact rec-table">
<thead><tr><th>Date</th><th>Test</th><th>Result</th><th>Flag</th><th>{source}</th></tr></thead>
<tbody>
<tr>
  <td data-label="Date">05 Aug 2026</td>
  <td data-label="Test">eGFR (kidney function)<span class="sub">Investigation</span></td>
  <td data-label="Result">107 mL/min/1.73m²<span class="sub">Reference &gt; 60</span></td>
  <td data-label="Flag"><span class="chip sla-green"><span class="shape">✓</span>Normal</span></td>
  <td data-label="{source}">Central Screening Laboratory<span class="sub">Sample history</span></td>
</tr>
<tr>
  <td data-label="Date">05 Aug 2026</td>
  <td data-label="Test">LDL cholesterol</td>
  <td data-label="Result">2.6 mmol/L</td>
  <td data-label="Flag"><span class="chip sla-green">Normal</span></td>
  <td data-label="{source}">Central Screening Laboratory</td>
</tr>
</tbody></table></div>
</body></html>"""

# The in-flow ::before label cannot be measured with a twin span (inserting one shifts the
# value). Instead: the first text/chip in the cell must start at least one label-line below
# the cell's content-box top — that is the visual proof the label has its own line.
MEASURE = """() => {
  const fails = [];
  const tds = [...document.querySelectorAll('td[data-label]')].filter(td => td.getAttribute('data-label'));
  for (const td of tds) {
    const s = getComputedStyle(td, '::before');
    const label = td.getAttribute('data-label');
    const fontSize = parseFloat(s.fontSize) || 12;
    const box = td.getBoundingClientRect();
    const padTop = parseFloat(getComputedStyle(td).paddingTop) || 0;
    const rg = document.createRange();
    const texts = [];
    const tw = document.createTreeWalker(td, NodeFilter.SHOW_TEXT);
    for (let n; (n = tw.nextNode());) {
      if (!n.nodeValue.trim()) continue;
      rg.selectNodeContents(n);
      for (const r of rg.getClientRects()) if (r.height > 1 && r.width > 1)
        texts.push({ t: n.nodeValue.trim().slice(0, 40), top: r.top, bottom: r.bottom, left: r.left, right: r.right });
    }
    for (const chip of td.querySelectorAll('.chip')) {
      const r = chip.getBoundingClientRect();
      if (r.height > 1) texts.push({ t: chip.textContent.trim().slice(0, 40), top: r.top, bottom: r.bottom, left: r.left, right: r.right });
    }
    if (!texts.length) { fails.push({ label, reason: 'no value text' }); continue; }
    const first = texts.reduce((a, b) => a.top < b.top ? a : b);
    const minTop = box.top + padTop + fontSize * 0.85;  // one line of 12 px label + a little leading
    if (first.top < minTop)
      fails.push({ label, value: first.t, valueTop: Math.round(first.top), minTop: Math.round(minTop),
                   tdTop: Math.round(box.top), beforePos: s.position, beforeDisplay: s.display });
    if (s.position !== 'static' && s.position !== 'relative')
      fails.push({ label, reason: 'label is not in-flow', beforePos: s.position });
    if (s.display !== 'block')
      fails.push({ label, reason: 'label is not display:block', beforeDisplay: s.display });
  }
  return { fails, n: tds.length, vw: document.documentElement.clientWidth,
           beforePos: tds[0] ? getComputedStyle(tds[0], '::before').position : null,
           beforeDisplay: tds[0] ? getComputedStyle(tds[0], '::before').display : null };
}"""


@pytest.mark.parametrize("width", [360, 390])
@pytest.mark.parametrize("lang,direction,source", [
    ("en", "ltr", "Source"),
    ("ar", "rtl", "المصدر والمختبر المسؤول عن النتيجة"),
    ("hi", "ltr", "स्रोत / केंद्रीय जांच प्रयोगशाला"),
    ("ur", "rtl", "منبع"),
])
def test_stacked_table_labels_do_not_overlap_values(width, lang, direction, source):
    from playwright.sync_api import sync_playwright

    html = FIXTURE.format(css=CSS, lang=lang, dir=direction, source=source)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        page = browser.new_context(viewport={"width": width, "height": 800}).new_page()
        page.set_content(html, wait_until="load")
        result = page.evaluate(MEASURE)
        browser.close()
    assert result["beforePos"] in {"static", "relative"}, result
    assert result["beforeDisplay"] == "block", result
    assert result["n"] == 10, result
    assert result["fails"] == [], result
