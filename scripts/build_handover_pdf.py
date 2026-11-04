"""Build docs/handover/KareShafaa_Handover_Package.pdf (and .docx) from the Markdown files in docs/handover/.
Needs: markdown, python-docx, playwright with Chrome/Chromium.  Run: python scripts/build_handover_pdf.py"""
from __future__ import annotations

import base64
import datetime as dt
import os
import re
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
H = ROOT / "docs" / "handover"
PARTS = ["README.md", "problem_brief.md", "runnable_project.md", "how_to_run_the_task.md", "test_results.md",
         "pilot_plan.md", "handover_session.md", "examples/example_run_record.md"]
LOGO = ROOT / "kareshafaa" / "static" / "brand" / "logo-horizontal.svg"
NAVY, MINT, TEAL = "#0A2B5E", "#4FD1A1", "#0A6B66"


def html() -> str:
    logo = "data:image/svg+xml;base64," + base64.b64encode(LOGO.read_bytes()).decode()
    body = []
    for p in PARTS:
        md = (H / p).read_text()
        md = re.sub(r"\[([^\]]+)\]\((?!http)[^)]+\)", r"\1", md)  # local links -> plain text in print
        body.append(f'<section class="part">{markdown.markdown(md, extensions=["tables", "fenced_code"])}</section>')
    today = dt.date.today().strftime("%d %B %Y")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>KareShafaa handover package</title><style>
@page {{ size: A4; margin: 18mm 16mm 18mm 16mm; }}
body {{ font: 10pt/1.45 "Segoe UI", "Noto Sans", Arial, sans-serif; color: #1b2430; }}
.cover {{ height: 245mm; display: flex; flex-direction: column; justify-content: center; page-break-after: always; }}
.cover img {{ width: 95mm; }}
.cover h1 {{ color: {NAVY}; font-size: 26pt; margin: 14mm 0 2mm; border: 0; }}
.cover .rule {{ height: 3px; width: 60mm; background: {MINT}; margin: 4mm 0 6mm; }}
.cover p {{ color: #3b4a5e; font-size: 12pt; margin: 1mm 0; }}
.part {{ page-break-before: always; }}
.part:first-of-type {{ page-break-before: avoid; }}
h1 {{ color: {NAVY}; font-size: 17pt; border-bottom: 3px solid {MINT}; padding-bottom: 2mm; }}
h2 {{ color: {NAVY}; font-size: 13pt; margin-top: 6mm; }} h3 {{ color: {TEAL}; font-size: 11pt; }}
table {{ border-collapse: collapse; width: 100%; margin: 3mm 0; font-size: 8.6pt; page-break-inside: auto; }}
th {{ background: {NAVY}; color: #fff; text-align: left; }} th, td {{ padding: 1.6mm 2mm; border: 1px solid #d5dbe3; vertical-align: top; }}
tr:nth-child(even) td {{ background: #f4f7fa; }}
code {{ font: 8.4pt Consolas, "DejaVu Sans Mono", monospace; background: #eef2f6; padding: 0 1px; word-break: break-all; }}
pre {{ background: #eef2f6; padding: 3mm; white-space: pre-wrap; font-size: 8.2pt; }}
blockquote {{ border-left: 4px solid {MINT}; margin: 3mm 0; padding: 1mm 4mm; background: #f2fbf7; }}
.foot {{ color: #6b7785; font-size: 8pt; }}
</style></head><body>
<div class="cover"><img src="{logo}" alt="KareShafaa"><h1>Handover package</h1><div class="rule"></div>
<p>Future Health Challenge 05 – closing the loop on abnormal screening results</p>
<p>Problem brief · runnable project · task agreement · test results · pilot plan · handover session</p>
<p class="foot" style="margin-top:12mm">Prepared {today}. All data synthetic. Values marked ASSUMPTION or CONFIRM are not yet agreed.</p></div>
{''.join(body)}</body></html>"""


def pdf(doc: str, out: Path) -> None:
    from playwright.sync_api import sync_playwright

    tmp = H / "_print.html"
    tmp.write_text(doc)
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch(channel=os.environ.get("KARESHAFAA_BROWSER_CHANNEL", "chrome") or None)
            p = b.new_page()
            p.goto(tmp.as_uri(), wait_until="load")
            p.pdf(path=str(out), format="A4", print_background=True, display_header_footer=True,
                  header_template="<span></span>",
                  footer_template='<div style="font-size:7pt;color:#6b7785;width:100%;padding:0 16mm;display:flex;justify-content:space-between">'
                                  '<span>KareShafaa handover package</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>',
                  margin={"top": "16mm", "bottom": "18mm", "left": "16mm", "right": "16mm"})
            b.close()
    finally:
        tmp.unlink(missing_ok=True)


def docx(out: Path) -> None:
    from docx import Document
    from docx.shared import Pt, RGBColor

    d = Document()
    st = d.styles["Normal"]
    st.font.name, st.font.size = "Calibri", Pt(10)
    d.add_heading("KareShafaa handover package", 0)
    d.add_paragraph("Future Health Challenge 05. All data synthetic. Values marked ASSUMPTION or CONFIRM are not yet agreed.")
    for p in PARTS:
        lines = (H / p).read_text().splitlines()
        d.add_page_break()
        i = 0
        while i < len(lines):
            ln = lines[i]
            if ln.startswith("```"):
                j = i + 1
                while j < len(lines) and not lines[j].startswith("```"):
                    j += 1
                para = d.add_paragraph("\n".join(lines[i + 1:j]))
                for r in para.runs:
                    r.font.name, r.font.size = "Consolas", Pt(8)
                i = j + 1
                continue
            if ln.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[-| :]+\|$", lines[i + 1]):
                rows = [ln]
                j = i + 2
                while j < len(lines) and lines[j].startswith("|"):
                    rows.append(lines[j])
                    j += 1
                cells = [[c.strip() for c in r.strip("|").split("|")] for r in rows]
                t = d.add_table(rows=len(cells), cols=len(cells[0]))
                t.style = "Light Grid Accent 1"
                for r_i, r in enumerate(cells):
                    for c_i, c in enumerate(r[:len(cells[0])]):
                        t.cell(r_i, c_i).text = _plain(c)
                i = j
                continue
            m = re.match(r"^(#+) (.*)", ln)
            if m:
                h = d.add_heading(_plain(m.group(2)), min(len(m.group(1)), 3))
                for r in h.runs:
                    r.font.color.rgb = RGBColor(0x0A, 0x2B, 0x5E)
            elif re.match(r"^\s*([*-]|\d+\.) ", ln):
                d.add_paragraph(_plain(re.sub(r"^\s*([*-]|\d+\.) (\[[ x]\] )?", "", ln)), style="List Bullet")
            elif ln.startswith(">"):
                d.add_paragraph(_plain(ln.lstrip("> ")), style="Intense Quote")
            elif ln.strip():
                d.add_paragraph(_plain(ln))
            i += 1
    d.save(str(out))


def _plain(s: str) -> str:
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    return s.replace("**", "").replace("`", "")


if __name__ == "__main__":
    pdf(html(), H / "KareShafaa_Handover_Package.pdf")
    docx(H / "KareShafaa_Handover_Package.docx")
    print("written", H / "KareShafaa_Handover_Package.pdf", H / "KareShafaa_Handover_Package.docx")
