#!/bin/bash
# Build the KareShafaa operating document: figures -> pass 1 -> PDF -> contents page numbers -> final docx + PDF.
# Screenshots (build/shots) come from shots.py against a running server; see README.md.
set -euo pipefail
cd "$(dirname "$0")"
OUT_DIR="${1:-build/out}"; mkdir -p "$OUT_DIR"
NAME=KareShafaa_Operating_Document
python3 diagrams.py
if [ -d build/shots ] && [ -n "$(ls build/shots 2>/dev/null)" ]; then python3 compose.py >/dev/null; fi  # else use committed assets/fig
rm -f build/toc_pages.json
python3 build.py build/out/pass1.docx
soffice --headless --convert-to pdf --outdir build/out build/out/pass1.docx >/dev/null 2>&1
python3 tocpages.py build/out/pass1.pdf
python3 build.py "$OUT_DIR/$NAME.docx"
soffice --headless --convert-to pdf --outdir "$OUT_DIR" "$OUT_DIR/$NAME.docx" >/dev/null 2>&1
cp build/toc_pages.json build/toc_pages.pass1.json
python3 tocpages.py "$OUT_DIR/$NAME.pdf"
if ! cmp -s build/toc_pages.json build/toc_pages.pass1.json; then  # page numbers moved: one more round
  python3 build.py "$OUT_DIR/$NAME.docx"
  soffice --headless --convert-to pdf --outdir "$OUT_DIR" "$OUT_DIR/$NAME.docx" >/dev/null 2>&1
fi
python3 check.py "$OUT_DIR/$NAME.docx" "$OUT_DIR/$NAME.pdf"
