"""Build the KareShafaa operating document (.docx). Usage: python build.py OUT.docx
Two-pass contents: tocpages.py writes build/toc_pages.json from a PDF of the first pass (see make.sh)."""
import json, sys
from pathlib import Path
from docx import Document
from docx.shared import Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from lib import *

HERE = Path(__file__).parent
BUILD = HERE / "build"; FIG = HERE / "assets" / "fig"; DIAG = BUILD / "diag"; LOGO = HERE / "assets" / "logo"
PAGES = json.loads((BUILD / "toc_pages.json").read_text()) if (BUILD / "toc_pages.json").exists() else {}
OUT = Path(sys.argv[1])
TEXT_W = 17.0
VERSION, DATE = "1.1", "28 October 2026"
FOOTER = ("KareShafaa v0.1 · Prototype software, not a certified medical device. "
          "Clinical decisions remain with the treating clinician.")

doc = Document()
sec = doc.sections[0]
sec.page_height, sec.page_width = Cm(29.7), Cm(21.0)
sec.left_margin = sec.right_margin = Cm(2.0)
sec.top_margin, sec.bottom_margin = Cm(2.2), Cm(2.0)
sec.header_distance, sec.footer_distance = Cm(0.8), Cm(0.8)
sec.different_first_page_header_footer = True

st = doc.styles
n = st["Normal"]; set_font(n, size=10.5, color=DARK)
n.paragraph_format.space_after = Pt(5); n.paragraph_format.line_spacing = 1.12
for name, size, color, before, after in (("Heading 1", 18, NAVY, 0, 8), ("Heading 2", 13, NAVY, 14, 5), ("Heading 3", 11.5, DARK, 10, 3)):
    s = st[name]; set_font(s, size=size, bold=True, color=color)
    s.paragraph_format.space_before = Pt(before); s.paragraph_format.space_after = Pt(after)
    s.paragraph_format.keep_with_next = True
for name in ("List Bullet", "List Number"):
    set_font(st[name], size=10.5, color=DARK)
    st[name].paragraph_format.space_after = Pt(2.5)

# ---------------------------------------------------------------- header / footer
hp = sec.header.paragraphs[0]
hp.paragraph_format.tab_stops.add_tab_stop(Cm(TEXT_W), WD_TAB_ALIGNMENT.RIGHT)
hp.add_run().add_picture(str(LOGO / "icon-small.png"), width=Cm(0.62))
r = hp.add_run("   KareShafaa  ·  Operating Document (SOP and User Operations Manual)"); set_font(r, size=8.5, color=NAVY, bold=True)
r = hp.add_run(f"\tVersion {VERSION}  ·  {DATE}"); set_font(r, size=8.5, color=GREY)
para_border(hp, "bottom", MINT_HEX, 8, 4)
fp = sec.footer.paragraphs[0]
fp.paragraph_format.tab_stops.add_tab_stop(Cm(TEXT_W), WD_TAB_ALIGNMENT.RIGHT)
para_border(fp, "top", LINE_HEX, 4, 4)
r = fp.add_run(FOOTER); set_font(r, size=7.5, color=GREY)
r = fp.add_run("\tPage "); set_font(r, size=8, color=GREY)
r = fp.add_run(); set_font(r, size=8, color=NAVY, bold=True); add_field(r, "PAGE")
r = fp.add_run(" of "); set_font(r, size=8, color=GREY)
r = fp.add_run(); set_font(r, size=8, color=GREY); add_field(r, "NUMPAGES")
# first page: footer notice only
ffp = sec.first_page_footer.paragraphs[0]
r = ffp.add_run(FOOTER); set_font(r, size=7.5, color=GREY)

# ---------------------------------------------------------------- helpers for content modules
HEADINGS = []
COUNT = {"fig": 0, "tab": 0}


def h1(text):
    p = doc.add_paragraph(style="Heading 1")
    p.paragraph_format.page_break_before = True
    add_rich(p, text); para_border(p, "bottom", MINT_HEX, 12, 4)
    HEADINGS.append((1, text)); return p


def h2(text):
    p = doc.add_paragraph(style="Heading 2"); add_rich(p, text); HEADINGS.append((2, text)); return p


def h3(text):
    p = doc.add_paragraph(style="Heading 3"); add_rich(p, text); return p


def para(text, size=None, color=None, align=None, after=None, italic=False, bold=False):
    p = doc.add_paragraph(); add_rich(p, text, size=size, color=color, italic=italic, base_bold=bold)
    if align == "center": p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if after is not None: p.paragraph_format.space_after = Pt(after)
    return p


def bullets(items, size=None):
    for it in items:
        p = doc.add_paragraph(style="List Bullet"); add_rich(p, it, size=size)


def steps(items, size=None):
    for i, it in enumerate(items, 1):
        p = doc.add_paragraph(); p.paragraph_format.left_indent = Cm(0.75); p.paragraph_format.first_line_indent = Cm(-0.55)
        p.paragraph_format.space_after = Pt(2.5)
        r = p.add_run(f"{i}.  "); set_font(r, bold=True, color=NAVY, size=size)
        add_rich(p, it, size=size)
    doc.paragraphs[-1].paragraph_format.space_after = Pt(6)


def checks(items, size=None):
    for it in items:
        p = doc.add_paragraph(); p.paragraph_format.left_indent = Cm(0.75); p.paragraph_format.first_line_indent = Cm(-0.55)
        p.paragraph_format.space_after = Pt(2.5)
        r = p.add_run("☐  "); set_font(r, color=NAVY, size=size)
        add_rich(p, it, size=size)
    doc.paragraphs[-1].paragraph_format.space_after = Pt(6)


def caption(kind, text):
    COUNT[kind] += 1
    p = doc.add_paragraph(); p.paragraph_format.space_after = Pt(3 if kind == "tab" else 9)
    if kind == "fig": p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(f"{'Figure' if kind == 'fig' else 'Table'} {COUNT[kind]}. "); set_font(r, size=8.5, bold=True, color=NAVY)
    add_rich(p, text, size=8.5, color=GREY, italic=True)
    if kind == "tab": keep_with_next(p)
    return p


def table(headers, rows, widths, title=None, size=9, zebra=True, first_col_bold=False, center_cols=(), header=True, valign="center"):
    if title: caption("tab", title)
    t = doc.add_table(rows=0, cols=len(widths)); t.alignment = WD_TABLE_ALIGNMENT.CENTER
    table_borders(t); cell_margins(t)
    allrows = ([headers] if header else []) + rows
    for ri, row in enumerate(allrows):
        cells = t.add_row().cells
        is_h = header and ri == 0
        for ci, val in enumerate(row):
            c = cells[ci]; c.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER if (is_h or valign == "center") else WD_CELL_VERTICAL_ALIGNMENT.TOP
            p = c.paragraphs[0]; p.paragraph_format.space_after = Pt(0); p.paragraph_format.line_spacing = 1.05
            if ci in center_cols: p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            if is_h:
                r = p.add_run(str(val)); set_font(r, size=size, bold=True, color=WHITE); shade(c, NAVY_HEX)
            else:
                lines = str(val).split("\n")
                for li, line in enumerate(lines):
                    if li: p = c.add_paragraph(); p.paragraph_format.space_after = Pt(0)
                    add_rich(p, line, size=size, base_bold=(first_col_bold and ci == 0))
                    if ci in center_cols: p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                if zebra and (ri - (1 if header else 0)) % 2 == 1: shade(c, TINT_HEX)
        rw = t.rows[-1]; no_split(rw)
        if is_h: repeat_header(rw)
    set_col_widths(t, widths)
    sp = doc.add_paragraph(); sp.paragraph_format.space_after = Pt(4); set_font(sp.add_run(""), size=4)
    return t


def figure(path, text, width_cm=16.0):
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER; keep_with_next(p)
    p.paragraph_format.space_before = Pt(4); p.paragraph_format.space_after = Pt(2)
    p.add_run().add_picture(str(path), width=Cm(width_cm))
    caption("fig", text)


def callout(title, lines, fill=MINT_TINT_HEX, edge=MINT_HEX):
    t = doc.add_table(rows=1, cols=1); t.alignment = WD_TABLE_ALIGNMENT.CENTER
    c = t.rows[0].cells[0]; shade(c, fill); cell_border(c, "left", edge, 30)
    for e in ("top", "bottom", "right"): cell_border(c, e, fill, 2)
    cell_margins(t, 90, 90, 160, 140)
    p = c.paragraphs[0]; p.paragraph_format.space_after = Pt(2)
    r = p.add_run(title); set_font(r, size=10, bold=True, color=NAVY)
    for ln in lines:
        q = c.add_paragraph(); q.paragraph_format.space_after = Pt(1.5); add_rich(q, ln, size=9.5)
    set_col_widths(t, [TEXT_W])
    sp = doc.add_paragraph(); sp.paragraph_format.space_after = Pt(4)


def page_break():
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


G = dict(checks=checks, h1=h1, h2=h2, h3=h3, para=para, bullets=bullets, steps=steps, table=table, figure=figure, callout=callout,
         caption=caption, page_break=page_break, FIG=FIG, DIAG=DIAG, GREY=GREY, NAVY=NAVY, doc=doc)

import frontmatter  # noqa: E402  title page, document control, contents placeholder
frontmatter.build(G, VERSION, DATE)
import content  # noqa: E402
content.build(G)

# ---------------------------------------------------------------- contents (static, two-pass page numbers)
anchor = G["TOC_ANCHOR"]
for lvl, text in HEADINGS:
    p = anchor.insert_paragraph_before()
    pf = p.paragraph_format
    pf.tab_stops.add_tab_stop(Cm(TEXT_W), WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS)
    pf.left_indent = Cm(0 if lvl == 1 else 0.6)
    pf.space_before = Pt(4 if lvl == 1 else 0); pf.space_after = Pt(0.5); pf.line_spacing = 1.0
    r = p.add_run(text); set_font(r, size=10.5 if lvl == 1 else 9.8, bold=(lvl == 1), color=NAVY if lvl == 1 else DARK)
    r = p.add_run("\t" + str(PAGES.get(text, "00"))); set_font(r, size=10.5 if lvl == 1 else 10, bold=(lvl == 1), color=DARK)
anchor._p.getparent().remove(anchor._p)

OUT.parent.mkdir(parents=True, exist_ok=True)
doc.save(str(OUT))
(BUILD / "headings.json").write_text(json.dumps(HEADINGS))
print("saved", OUT, "headings", len(HEADINGS), "figures", COUNT["fig"], "tables", COUNT["tab"])
