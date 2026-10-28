"""Read heading page numbers from a PDF of the first pass -> build/toc_pages.json."""
import json, subprocess, sys
from pathlib import Path
B = Path(__file__).parent / "build"
pdf = sys.argv[1]
n = int([l for l in subprocess.run(["pdfinfo", pdf], capture_output=True, text=True).stdout.splitlines() if l.startswith("Pages")][0].split()[-1])
pages = []
for i in range(1, n + 1):
    t = subprocess.run(["pdftotext", "-layout", "-f", str(i), "-l", str(i), pdf, "-"], capture_output=True, text=True).stdout
    pages.append([" ".join(l.split()) for l in t.splitlines()])
out, miss = {}, []
for lvl, h in json.loads((B / "headings.json").read_text()):
    hit = None
    for i, lines in enumerate(pages, 1):
        if any((l == h or l.startswith(h)) and "...." not in l and len(l) <= len(h) + 2 for l in lines):
            hit = i  # last match: contents lines carry dot leaders and are skipped
    (miss.append(h) if hit is None else out.__setitem__(h, hit))
(B / "toc_pages.json").write_text(json.dumps(out, indent=0, ensure_ascii=False))
vals = list(out.values())
print("pages", n, "headings", len(out), "missing", miss, "monotonic", all(a <= b for a, b in zip(vals, vals[1:])))
