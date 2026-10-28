"""Rebuild every logo file and app icon from the hand-built SVG source in scripts/brand/.

usage: LOGO_OUT=/tmp/logo python3 scripts/make_pwa_icons.py
needs: fontTools (wordmark outlines from Figtree, SIL OFL 1.1), Pillow (favicon.ico), Playwright + Chrome (exact SVG
rendering). Output is copied into kareshafaa/static/brand/, kareshafaa/static/icons/ and kareshafaa/static/favicon.*
Brand rules: docs/BRAND.md (also shipped as BRAND.md in the logo pack). Latin script only (no Arabic lettering in the mark or wordmark).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts" / "brand"
STATIC = ROOT / "kareshafaa" / "static"


def main() -> None:
    out = Path(os.environ.setdefault("LOGO_OUT", "/tmp/kareshafaa_logo")); (out / "png").mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, str(SRC / "build_all.py")], check=True, cwd=SRC)
    jobs = [("icon-small.svg", f"png/icon-{s}.png", s, s) for s in (16, 32, 48)]
    jobs += [("icon.svg", f"png/icon-{s}.png", s, s) for s in (192, 512, 1024)]
    jobs += [("_apple.svg", "png/apple-touch-icon-180.png", 180, 180), ("_maskable.svg", "png/icon-maskable-512.png", 512, 512),
             ("_og.svg", "png/og-1200x630.png", 1200, 630)]
    args = [str(x) for j in jobs for x in (out / j[0], out / j[1], j[2], j[3])]
    subprocess.run([sys.executable, str(SRC / "render.py"), *args], check=True)
    from PIL import Image
    for n in ("apple-touch-icon-180", "icon-maskable-512", "og-1200x630"):  # opaque
        Image.open(out / f"png/{n}.png").convert("RGB").save(out / f"png/{n}.png")
    ims = [Image.open(out / f"png/icon-{s}.png").convert("RGBA") for s in (16, 32, 48)]
    ims[2].save(out / "favicon.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48)], append_images=ims[:2])
    for f in out.glob("*.svg"):
        if not f.name.startswith("_"):
            shutil.copy(f, STATIC / "brand" / f.name)
    shutil.copy(out / "png/og-1200x630.png", STATIC / "brand")
    for n in ("icon-16", "icon-32", "icon-48", "icon-192", "icon-512", "icon-maskable-512", "apple-touch-icon-180"):
        shutil.copy(out / f"png/{n}.png", STATIC / "icons")
    shutil.copy(out / "icon-small.svg", STATIC / "favicon.svg"); shutil.copy(out / "favicon.ico", STATIC / "favicon.ico")


if __name__ == "__main__":
    main()
