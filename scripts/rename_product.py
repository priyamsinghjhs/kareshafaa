"""Rename the product in one repeatable step (used 2026-10-22 for Wasl -> KareShafaa; OLD_* below are the pre-rename names). Paused earlier attempt: branch wip/rename-dhruva-health.

usage (clean git tree, servers stopped):
  python scripts/rename_product.py --brand "Dhruva Health" --short Dhruva --package dhruva --env-prefix DHRUVA \
      --meaning "Sanskrit for the pole star: the fixed guiding point, so no patient is lost on the way to care" \
      [--dry-run]

What it does (each step is idempotent-safe on a clean tree; review `git diff --stat` afterwards):
 1. git mv  wasl/ -> <package>/, wasl_reference/ -> <package>_reference/, static/wasl.css -> static/<package>.css
 2. text replacement in every tracked text file EXCEPT ITERATIONS.md history (a rename note is appended instead):
      "Wasl - Closing the Screening-to-Care Loop" -> "<brand> - ..."   (OpenAPI title, Makefile header)
      wasl_reference -> <package>_reference, WASL_ -> <PREFIX>_, WASL -> <PREFIX>, Wasl -> <short>, wasl -> <package>
 3. brand fixes: page <title>, wordmark (Arabic logo glyph replaced by a neutral mark), footer, README name note,
    Dockerfile header + OCI labels, model-card heading, package docstring
 4. compatibility: legacy WASL_* environment variables (and WASL_* keys in .env) are still accepted; <PREFIX>_* wins
 5. data/: WAL-checkpoint and rename data/wasl*.db and data/wasl_edge_manifest.json to the new prefix (data kept)
Then (printed at the end): full test suite, validator on all DBs, `make seed` (demo DB, so model card/messages show
the new name), restart `make run`, curl / and /queue, update ../Challenge05_Solution_Design.md naming section and
regenerate the .docx (pypandoc; mermaid figures under ../figures contain the name too), build the source archive.
Patient message templates contain no brand name today (nothing to translate); the simulated link host
"wasl.example" is renamed by step 2.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD_PKG, OLD_SHORT, OLD_PREFIX = "wasl", "Wasl", "WASL"


def sh(args: list[str], dry: bool) -> None:
    print("$", " ".join(args))
    if not dry:
        subprocess.run(args, cwd=ROOT, check=True)


def replace_in(path: Path, pairs: list[tuple[str, str]], dry: bool) -> bool:
    try:
        s = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return False
    o = s
    for a, b in pairs:
        s = s.replace(a, b)
    if s != o and not dry:
        path.write_text(s, encoding="utf-8")
    return s != o


def sub_once(path: Path, old: str, new: str, dry: bool) -> None:
    s = path.read_text(encoding="utf-8")
    if old not in s:
        print(f"  ! not found in {path.relative_to(ROOT)}: {old[:60]!r} (check by hand)")
        return
    if not dry:
        path.write_text(s.replace(old, new, 1), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", required=True)
    ap.add_argument("--short", required=True)
    ap.add_argument("--package", required=True)
    ap.add_argument("--env-prefix", required=True)
    ap.add_argument("--meaning", default="")
    ap.add_argument("--date", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    dry, pkg, short, brand, pfx = a.dry_run, a.package, a.short, a.brand, a.env_prefix.rstrip("_")
    if not dry and subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
                                  text=True).stdout.strip():
        raise SystemExit("git tree not clean: commit or stash first")

    # 1. moves
    sh(["git", "mv", OLD_PKG, pkg], dry)
    sh(["git", "mv", f"{OLD_PKG}_reference", f"{pkg}_reference"], dry)
    sh(["git", "mv", f"{pkg if not dry else OLD_PKG}/static/{OLD_PKG}.css", f"{pkg if not dry else OLD_PKG}/static/{pkg}.css"], dry)

    # 2. replacements
    pairs = [(f"{OLD_SHORT} - Closing the Screening-to-Care Loop", f"{brand} - Closing the Screening-to-Care Loop"),
             (f"{OLD_SHORT} — Closing the Screening-to-Care Loop", f"{brand} — Closing the Screening-to-Care Loop"),
             (f"{OLD_PKG}_reference", f"{pkg}_reference"), (f"{OLD_PREFIX}_", f"{pfx}_"), (OLD_PREFIX, pfx),
             (OLD_SHORT, short), (OLD_PKG, pkg)]
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    skip = {"ITERATIONS.md", "scripts/rename_product.py"}
    changed = [f for f in files if f not in skip and replace_in(ROOT / f, pairs, dry)]
    print(f"text replaced in {len(changed)} files")

    # 3. brand fixes
    base = ROOT / pkg / "templates" / "base.html"
    if not dry:
        sub_once(base, f"<title>{{{{ title }}}} · {short}</title>", f"<title>{{{{ title }}}} · {brand}</title>", dry)
        sub_once(base, f'<span class="logo">وصل</span> <strong>{short}</strong>',
                 f'<span class="logo" aria-hidden="true">✦</span> <strong>{brand}</strong>', dry)
        sub_once(base, f"    {short} v{{{{ version }}}} ·", f"    {brand} v{{{{ version }}}} ·", dry)
        sub_once(ROOT / "README.md", f"# {short} (وصل) – Closing the Screening-to-Care Loop",
                 f"# {brand} – Closing the Screening-to-Care Loop\n\n> **Name.** *{short}*: {a.meaning}. Renamed "
                 f"from \"{OLD_SHORT}\"{(' on ' + a.date) if a.date else ''} (\"wasl\" is also the brand of a Dubai "
                 f"real-estate developer). \"{brand}\" still needs UAE and India trademark clearance. The brand name "
                 "is never translated in patient messages.", dry)
        sub_once(ROOT / pkg / "ml" / "registry.py", f"# Model card - {short} outreach", f"# Model card - {brand} outreach", dry)
        init = ROOT / pkg / "__init__.py"
        sub_once(init, f'"""{short} - closing', f'"""{brand} - closing', dry)
        init.write_text(init.read_text(encoding="utf-8") + LEGACY_ENV.replace("PFX", pfx), encoding="utf-8")
        dock = ROOT / "Dockerfile"
        s = dock.read_text(encoding="utf-8")
        s = s.replace(f"# {short} prototype image", f"# {brand} prototype image", 1).replace(
            "FROM python:3.12-slim\n", f'FROM python:3.12-slim\nLABEL org.opencontainers.image.title="{brand}" \\\n'
            f'      org.opencontainers.image.description="{brand} - closing the screening-to-care loop (prototype, '
            'synthetic data only)"\n', 1)
        dock.write_text(s, encoding="utf-8")
        (ROOT / "tests" / "test_rename_compat.py").write_text(COMPAT_TEST.replace("PKG", pkg).replace("PFX", pfx),
                                                              encoding="utf-8")
        with open(ROOT / "ITERATIONS.md", "a", encoding="utf-8") as f:
            f.write(f"\n> **Product renamed {OLD_SHORT} → {brand}** ({a.date or 'see git log'}). Entries above are kept "
                    f"as written: their `{OLD_PKG}/...` paths and `{OLD_PREFIX}_*` variables now read `{pkg}/...` and "
                    f"`{pfx}_*` (legacy `{OLD_PREFIX}_*` variables are still accepted).\n")

    # 5. data files (not in git)
    data = ROOT / "data"
    for db in sorted(data.glob(f"{OLD_PKG}*.db")):
        new = db.with_name(db.name.replace(OLD_PKG, pkg, 1))
        print(f"data: {db.name} -> {new.name}")
        if not dry:
            c = sqlite3.connect(db)
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            c.close()
            for suf in ("-wal", "-shm"):
                p = Path(str(db) + suf)
                if p.exists() and p.stat().st_size == 0:
                    p.unlink()
            os.rename(db, new)
    for m in data.glob(f"{OLD_PKG}_*manifest*.json"):
        print(f"data: {m.name} -> {m.name.replace(OLD_PKG, pkg, 1)}")
        if not dry:
            os.rename(m, m.with_name(m.name.replace(OLD_PKG, pkg, 1)))
    print("\nNext: .venv/bin/python -m pytest -q tests; validator on every data/*.db "
          f"({pfx}_DATABASE_URL=sqlite:///./data/<db> .venv/bin/python scripts/validate_data.py); make seed; make run;"
          f" curl localhost:8000/ ; grep -rin '{OLD_PKG}' over git ls-files and justify leftovers; design doc + docx;"
          " source archive.")


LEGACY_ENV = '''

def _accept_legacy_env() -> None:
    """Renamed from Wasl: settings use the PFX_ prefix; legacy WASL_* variables (and WASL_* keys in .env) are still
    accepted as a fallback (PFX_* wins if both are set)."""
    import os

    for key, value in list(os.environ.items()):
        if key.startswith("WASL_"):
            os.environ.setdefault("PFX_" + key[len("WASL_"):], value)
    try:
        with open(".env", encoding="utf-8") as f:
            for line in f:
                key, sep, value = line.strip().partition("=")
                if sep and key.startswith("WASL_"):
                    os.environ.setdefault("PFX_" + key[len("WASL_"):], value.strip().strip('"').strip("'"))
    except OSError:
        pass


_accept_legacy_env()
'''

COMPAT_TEST = '''"""Product renamed: legacy WASL_* settings still work; PFX_* wins."""
import os
import subprocess
import sys


def _setting(env: dict, name: str) -> str:
    code = f"import PKG; from PKG.config import Settings; print(getattr(Settings(), {name!r}))"
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("WASL_", "PFX_"))}
    return subprocess.run([sys.executable, "-c", code], env={**clean, **env}, capture_output=True, text=True,
                          check=True, cwd=os.path.dirname(os.path.dirname(__file__))).stdout.strip()


def test_legacy_wasl_env_is_accepted():
    assert _setting({"WASL_REQUEST_TIMEOUT_S": "7"}, "request_timeout_s") == "7.0"


def test_new_prefix_wins_over_legacy():
    assert _setting({"WASL_REQUEST_TIMEOUT_S": "7", "PFX_REQUEST_TIMEOUT_S": "9"}, "request_timeout_s") == "9.0"


def test_default_database_uses_new_name():
    assert _setting({}, "database_url").endswith("data/PKG.db")
'''

if __name__ == "__main__":
    main()
