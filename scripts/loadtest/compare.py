"""Before/after table from two load-test roots: python scripts/loadtest/compare.py /tmp/lt/baseline /tmp/lt-after 500 5000 20000
Each cell is "before → after"."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from summarize import table  # noqa: E402


def main() -> int:
    before, after, scales = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:]
    hb, rb = table(before, scales)
    _, ra = table(after, scales)
    out = ["| Scale (open cases) | " + " | ".join(hb) + " |", "|---|" + "---|" * len(scales)]
    for (label, vb), (_, va) in zip(rb, ra):
        cells = [b if b == a else f"{b} → **{a}**" for b, a in zip(vb, va)]
        out.append(f"| {label} | " + " | ".join(cells) + " |")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
