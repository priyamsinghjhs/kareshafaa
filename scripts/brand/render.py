"""Render SVG files to PNG with headless Chrome (exact SVG rendering, transparent background)."""
import sys, pathlib
from playwright.sync_api import sync_playwright
def render(jobs):  # [(svg_path, png_path, w, h)]
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome")
        for svg, png, w, h in jobs:
            p = b.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
            data = pathlib.Path(svg).read_text()
            p.set_content(f'<html><body style="margin:0;background:transparent">'
                          f'<img src="data:image/svg+xml;base64,{__import__("base64").b64encode(data.encode()).decode()}" style="width:{w}px;height:{h}px;display:block"></body></html>')
            p.wait_for_timeout(100)
            p.screenshot(path=png, omit_background=True, clip={"x": 0, "y": 0, "width": w, "height": h}); p.close()
        b.close()
if __name__ == "__main__":
    a = sys.argv[1:]; render([(a[i], a[i+1], int(a[i+2]), int(a[i+3])) for i in range(0, len(a), 4)])
