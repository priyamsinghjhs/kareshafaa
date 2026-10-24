"""Build every KareShafaa logo file from the hand-built geometry in build_icon.py + Figtree outlines."""
import pathlib, subprocess, sys
from build_icon import NAVY, MINT, WHITE, mark, badge, svg
from wordmark import wordmark
import os
OUT = pathlib.Path(os.environ.get("LOGO_OUT", "/workspace/fhdic/dist/logo")); OUT.mkdir(exist_ok=True)
D, WW, CAP, UPM = wordmark()
ASC = 760  # tallest letters (K, h, f) above the baseline, font units

def word(x, baseline, cap_px, color):
    k = cap_px / CAP
    return f'<path transform="translate({x:.1f} {baseline:.1f}) scale({k:.5f})" fill="{color}" d="{D}"/>', WW * k

def mono_badge(color, ident):
    m = mark(fg="#000", accent="#000", small=False, cut="#fff")
    t = f'translate(256 256) scale(0.92) translate({-(78+456)/2} {-(98+458)/2})'
    return (f'<defs><mask id="{ident}" maskUnits="userSpaceOnUse" x="0" y="0" width="512" height="512">'
            f'<rect width="512" height="512" rx="112" fill="#fff"/><g transform="{t}">{m}</g></mask></defs>'
            f'<rect width="512" height="512" rx="112" fill="{color}" mask="url(#{ident})"/>')

def horizontal(b, text_color):
    cap = 0.31 * 512; gap = 0.22 * 512
    w, ww = word(512 + gap, 256 + cap / 2, cap, text_color)
    W = 512 + gap + ww
    return svg(f'<g>{b}</g>\n  {w}', round(W), 512)

def stacked(b, text_color):
    cap = 0.30 * 512; k = cap / CAP; ww = WW * k
    W = max(ww, 512) ; gap = 0.16 * 512
    H = 512 + gap + ASC * k + 8
    w, _ = word((W - ww) / 2, 512 + gap + ASC * k, cap, text_color)
    return svg(f'<g transform="translate({(W-512)/2:.1f} 0)">{b}</g>\n  {w}', round(W), round(H))

KEY = '<rect x="4" y="4" width="504" height="504" rx="108" fill="none" stroke="#FFFFFF" stroke-opacity=".35" stroke-width="8"/>'  # keyline so the navy badge edge shows on dark backgrounds
dark = badge(scale=0.92) + KEY
files = {
    "icon.svg": svg(badge(scale=0.92), 512, 512),
    "icon-small.svg": svg(badge(small=True, scale=0.94), 512, 512),
    "icon-on-dark.svg": svg(dark, 512, 512),
    "icon-mono-white.svg": svg(mono_badge(WHITE, "kw"), 512, 512),
    "icon-mono-navy.svg": svg(mono_badge(NAVY, "kn"), 512, 512),
    "logo-horizontal.svg": horizontal(badge(scale=0.92), NAVY),
    "logo-horizontal-on-dark.svg": horizontal(dark, WHITE),
    "logo-horizontal-mono-white.svg": horizontal(mono_badge(WHITE, "hw"), WHITE),
    "logo-horizontal-mono-navy.svg": horizontal(mono_badge(NAVY, "hn"), NAVY),
    "logo-stacked.svg": stacked(badge(scale=0.92), NAVY),
    "logo-stacked-on-dark.svg": stacked(dark, WHITE),
    "logo-stacked-mono-white.svg": stacked(mono_badge(WHITE, "sw"), WHITE),
    "logo-stacked-mono-navy.svg": stacked(mono_badge(NAVY, "sn"), NAVY),
    # app icons: full-bleed squares (iOS/Android apply their own mask)
    "_apple.svg": svg(badge(rx=0, scale=0.80), 512, 512),
    "_maskable.svg": svg(badge(rx=0, scale=0.64), 512, 512),
}
for n, s in files.items():
    (OUT / n).write_text(s)
# OG / social image 1200x630: white card, horizontal logo centred
h = (OUT / "logo-horizontal.svg").read_text()
import re
hw, hh = map(int, re.search(r'width="(\d+)" height="(\d+)"', h).groups())
inner = h.split("</title>", 1)[1].rsplit("</svg>", 1)[0]
sc = 900 / hw
og = svg(f'<rect width="1200" height="630" fill="#FFFFFF"/><rect y="618" width="1200" height="12" fill="{MINT}"/>'
         f'<g transform="translate({(1200-900)/2} {(630-hh*sc)/2:.1f}) scale({sc:.5f})">{inner}</g>', 1200, 630)
(OUT / "_og.svg").write_text(og)
print({n: len(s) for n, s in files.items()})
