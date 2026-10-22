"""Generate the PWA icons (Latin-only brand: the icon is the neutral favicon mark, no lettering or Arabic script).

usage: python3 scripts/make_pwa_icons.py   (needs Pillow; output committed under kareshafaa/static/icons/)
The mark mirrors static/favicon.svg: a white arc joining two dots (result -> care) on the brand teal #00796b.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "kareshafaa" / "static" / "icons"
TEAL, WHITE = (0, 121, 107, 255), (255, 255, 255, 255)
SS = 4  # supersampling for smooth edges


def _bezier(p0, p1, p2, p3, n=400):
    pts = []
    for i in range(n + 1):
        t = i / n
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t ** 2, t ** 3
        pts.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return pts


def mark(size: int, *, maskable: bool, fill: float = 0.62) -> Image.Image:
    S = size * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if maskable:  # full-bleed background; the mark stays inside the 80% safe zone
        d.rectangle([0, 0, S, S], fill=TEAL)
        scale, off = S * fill / 64, S * (1 - fill) / 2
    else:
        d.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 14 / 64), fill=TEAL)
        scale, off = S / 64, 0
    P = lambda x, y: (off + x * scale, off + y * scale)  # noqa: E731
    # favicon path: M14 34 c6 0 8-12 18-12 s12 12 18 12
    curve = _bezier(P(14, 34), P(20, 34), P(22, 22), P(32, 22)) + _bezier(P(32, 22), P(42, 22), P(44, 34), P(50, 34))
    w = 6 * scale
    for cx, cy in curve:  # stamp round brush along the curve (round caps/joins, no seams)
        d.ellipse([cx - w / 2, cy - w / 2, cx + w / 2, cy + w / 2], fill=WHITE)
    for x, y, r in ((14, 34, 5), (50, 34, 5), (32, 22, 3)):
        cx, cy = P(x, y)
        rr = r * scale if r == 5 else w / 2
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=WHITE)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    mark(192, maskable=False).save(OUT / "icon-192.png")
    mark(512, maskable=False).save(OUT / "icon-512.png")
    mark(512, maskable=True).save(OUT / "icon-maskable-512.png")
    mark(180, maskable=True, fill=0.84).convert("RGB").save(OUT / "apple-touch-icon-180.png")  # iOS: opaque, square
    print("wrote", sorted(p.name for p in OUT.iterdir()))


if __name__ == "__main__":
    main()
