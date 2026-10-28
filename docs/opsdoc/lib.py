"""python-docx helpers for the KareShafaa operating document (brand: docs/BRAND.md)."""
import re
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

FONT = "Calibri"
NAVY_HEX, MINT_HEX = "0A2B5E", "4FD1A1"
NAVY, MINT = RGBColor(0x0A, 0x2B, 0x5E), RGBColor(0x4F, 0xD1, 0xA1)
DARK, GREY = RGBColor(0x1F, 0x29, 0x37), RGBColor(0x5B, 0x67, 0x78)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
TINT_HEX, LINE_HEX, MINT_TINT_HEX, NAVY_TINT_HEX = "F3F6FA", "C9D3E0", "EAF8F2", "E7EDF6"


def set_font(obj, size=None, bold=None, color=None, italic=None, name=FONT):
    f = obj.font
    f.name = name
    rpr = obj.element.get_or_add_rPr() if hasattr(obj.element, "get_or_add_rPr") else obj.element.rPr
    if rpr is not None:
        rf = rpr.find(qn("w:rFonts"))
        if rf is None:
            rf = OxmlElement("w:rFonts"); rpr.append(rf)
        for a in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            rf.set(qn(a), name)
    if size is not None: f.size = Pt(size)
    if bold is not None: f.bold = bold
    if italic is not None: f.italic = italic
    if color is not None: f.color.rgb = color


def shade(cell, hex_fill):
    tcPr = cell._tc.get_or_add_tcPr()
    for old in tcPr.findall(qn("w:shd")): tcPr.remove(old)
    shd = OxmlElement("w:shd"); shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), hex_fill)
    tcPr.append(shd)


def cell_margins(table, top=55, bottom=55, left=85, right=85):
    tblPr = table._tbl.tblPr
    m = OxmlElement("w:tblCellMar")
    for k, v in (("top", top), ("left", left), ("bottom", bottom), ("right", right)):
        e = OxmlElement(f"w:{k}"); e.set(qn("w:w"), str(v)); e.set(qn("w:type"), "dxa"); m.append(e)
    tblPr.append(m)


def table_borders(table, color=LINE_HEX, sz=4, inside=True):
    tblPr = table._tbl.tblPr
    b = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right") + (("insideH", "insideV") if inside else ()):
        e = OxmlElement(f"w:{edge}"); e.set(qn("w:val"), "single"); e.set(qn("w:sz"), str(sz)); e.set(qn("w:color"), color)
        b.append(e)
    tblPr.append(b)


def cell_border(cell, edge, color, sz=18):
    tcPr = cell._tc.get_or_add_tcPr()
    b = tcPr.find(qn("w:tcBorders"))
    if b is None:
        b = OxmlElement("w:tcBorders"); tcPr.append(b)
    e = OxmlElement(f"w:{edge}"); e.set(qn("w:val"), "single"); e.set(qn("w:sz"), str(sz)); e.set(qn("w:color"), color)
    b.append(e)


def repeat_header(row):
    trPr = row._tr.get_or_add_trPr(); e = OxmlElement("w:tblHeader"); e.set(qn("w:val"), "true"); trPr.append(e)


def no_split(row):
    trPr = row._tr.get_or_add_trPr(); e = OxmlElement("w:cantSplit"); e.set(qn("w:val"), "true"); trPr.append(e)


def set_col_widths(table, widths_cm):
    table.autofit = False
    tblPr = table._tbl.tblPr
    lay = OxmlElement("w:tblLayout"); lay.set(qn("w:type"), "fixed"); tblPr.append(lay)
    grid = table._tbl.tblGrid
    for i, gc in enumerate(grid.findall(qn("w:gridCol"))):
        if i < len(widths_cm): gc.set(qn("w:w"), str(int(widths_cm[i] * 567)))
    for row in table.rows:
        for i, c in enumerate(row.cells):
            if i < len(widths_cm): c.width = Cm(widths_cm[i])


def add_field(run, instr):
    for kind, text in (("begin", None), (None, instr), ("separate", None), (None, "1"), ("end", None)):
        if kind:
            e = OxmlElement("w:fldChar"); e.set(qn("w:fldCharType"), kind); run._r.append(e)
        elif text == instr:
            e = OxmlElement("w:instrText"); e.set(qn("xml:space"), "preserve"); e.text = f" {instr} "; run._r.append(e)
        else:
            e = OxmlElement("w:t"); e.text = text; run._r.append(e)


def para_border(p, edge="bottom", color=MINT_HEX, sz=12, space=4):
    pPr = p._p.get_or_add_pPr()
    b = pPr.find(qn("w:pBdr"))
    if b is None:
        b = OxmlElement("w:pBdr"); pPr.append(b)
    e = OxmlElement(f"w:{edge}"); e.set(qn("w:val"), "single"); e.set(qn("w:sz"), str(sz)); e.set(qn("w:space"), str(space)); e.set(qn("w:color"), color)
    b.append(e)


def keep_with_next(p, on=True):
    p.paragraph_format.keep_with_next = on


_TOK = re.compile(r"(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`)")


def add_rich(p, text, size=None, color=None, base_bold=False, italic=False):
    """**bold**, *italic*, `code` (Consolas)."""
    for part in _TOK.split(text):
        if not part:
            continue
        b, it, name = base_bold, italic, FONT
        if part.startswith("**") and part.endswith("**"): part, b = part[2:-2], True
        elif part.startswith("`") and part.endswith("`"): part, name = part[1:-1], "Consolas"
        elif part.startswith("*") and part.endswith("*") and len(part) > 2: part, it = part[1:-1], True
        r = p.add_run(part)
        set_font(r, size=(size - 0.5 if name == "Consolas" and size else size), bold=b, italic=it, color=color, name=name)
    return p
