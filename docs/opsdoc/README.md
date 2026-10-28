# KareShafaa operating document (SOP and user operations manual)

Source for `KareShafaa_Operating_Document.docx` / `.pdf` (copies of the last build are in `dist/`).
Every fact comes from the code and docs in this repository; where the solution design differs, the code is followed.

| File | Purpose |
|---|---|
| `make.sh [OUT_DIR]` | Full build: diagrams → figures → pass 1 → PDF → contents page numbers → final `.docx` + `.pdf` → checks |
| `build.py` | Page setup, brand styles, header/footer, helpers, contents (two-pass page numbers) |
| `frontmatter.py` | Title page (horizontal logo), document control, contents placeholder |
| `content_a.py` … `content_f.py` | Sections 1–2, 3, 4.1–4.3, 4.4–4.11, 5, 6, 7 |
| `lib.py` | python-docx helpers, brand colours (navy `#0A2B5E`, mint `#4FD1A1`, see `docs/BRAND.md`) |
| `diagrams.py` | End-to-end flow and escalation ladder (matplotlib, Carlito) → `build/diag/` |
| `shots.py` | Fresh screenshots of a running server, per role, laptop + phone → `build/shots/` |
| `lockshot.py` | Patient page with locked answers. Sends answers, so run it only against a scratch database copy |
| `compose.py` | Laptop/phone figure pairs and crops → `assets/fig/` (committed, so the document builds without a server) |
| `tocpages.py` | Reads heading pages from the pass-1 PDF |
| `check.py` | Release checks: forbidden wording, no password from the credentials file, footer on every page |

Requirements: python-docx, matplotlib, Pillow, LibreOffice (`soffice`), poppler-utils; for screenshots Playwright + Chrome.

```bash
# optional: new screenshots (server on :8000; the credentials file is read in memory, never printed)
KS_CRED_FILE=/path/to/credentials.tsv python shots.py http://localhost:8000
KS_CRED_FILE=/path/to/credentials.tsv python lockshot.py http://localhost:8011   # scratch copy only
# build (uses assets/fig when build/shots is empty)
KS_CRED_FILE=/path/to/credentials.tsv ./make.sh dist
```
Never put a password in this folder or in the document.
