"""Wordmark 'KareShafaa' as SVG paths from Figtree (SIL OFL 1.1), weight 800, with GPOS pair kerning via HarfBuzz-free shaping."""
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
import os
SRC = os.environ.get("FIGTREE_TTF", "/usr/share/fonts/truetype/sand-box/google/Figtree/Figtree-VariableFont_wght.ttf")  # Figtree, SIL OFL 1.1
def wordmark(text="KareShafaa", weight=800, tracking=-10):
    f = instantiateVariableFont(TTFont(SRC), {"wght": weight})
    gs = f.getGlyphSet(); cmap = f.getBestCmap(); upm = f["head"].unitsPerEm
    names = [cmap[ord(ch)] for ch in text]
    kern = {}
    try:  # pair adjustments from GPOS (format 1 + 2)
        for lk in f["GPOS"].table.LookupList.Lookup:
            for st in lk.SubTable:
                st = getattr(st, "ExtSubTable", st)
                if getattr(st, "LookupType", lk.LookupType) != 2: continue
                cov = st.Coverage.glyphs
                if st.Format == 1:
                    for i, a in enumerate(cov):
                        for pv in st.PairSet[i].PairValueRecord:
                            kern.setdefault((a, pv.SecondGlyph), getattr(pv.Value1, "XAdvance", 0) or 0)
                else:
                    c1, c2 = st.ClassDef1.classDefs, st.ClassDef2.classDefs
                    for a in names:
                        for b in names:
                            if a in cov:
                                r = st.Class1Record[c1.get(a, 0)].Class2Record[c2.get(b, 0)]
                                v = getattr(r.Value1, "XAdvance", 0) or 0
                                if v: kern.setdefault((a, b), v)
    except KeyError:
        pass
    pen = SVGPathPen(gs); x = 0
    for i, n in enumerate(names):
        gs[n].draw(TransformPen(pen, (1, 0, 0, -1, x, 0)))
        x += f["hmtx"][n][0] + tracking + (kern.get((n, names[i+1]), 0) if i + 1 < len(names) else 0)
    asc = f["OS/2"].sCapHeight
    return pen.getCommands(), x - tracking, asc, upm
if __name__ == "__main__":
    d, w, cap, upm = wordmark(); print(w, cap, upm, len(d))
