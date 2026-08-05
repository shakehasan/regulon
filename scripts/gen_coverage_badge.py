#!/usr/bin/env python3
"""Generate a shields.io endpoint-badge JSON from real coverage.py results.

Run after `pytest --cov` (via `make test`) has produced a `.coverage` data file. Never
hand-edit `.github/badges/coverage.json` — regenerate it with `make badge-coverage`. This
mirrors the project-wide rule that every published number comes from a real run, never a
hand-typed one (AGENTS.md, "Reports and metrics").

The README embeds the badge via shields.io's endpoint schema, pointed at this file's raw
GitHub URL, so the badge always reflects the coverage of the last commit that regenerated it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BADGE_PATH = REPO_ROOT / ".github" / "badges" / "coverage.json"


def coverage_percent() -> float:
    """Return the total coverage percentage from the last `coverage run` in this checkout."""
    result = subprocess.run(
        [sys.executable, "-m", "coverage", "report", "--format=total"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def color_for(pct: float) -> str:
    """Map a coverage percentage to a shields.io badge color."""
    if pct >= 90:
        return "brightgreen"
    if pct >= 80:
        return "green"
    if pct >= 70:
        return "yellow"
    if pct >= 50:
        return "orange"
    return "red"


def main() -> int:
    """Write the shields.io endpoint badge JSON. Returns the process exit code."""
    try:
        pct = coverage_percent()
    except (subprocess.CalledProcessError, ValueError) as exc:
        print(f"gen_coverage_badge: no coverage data found — run `make test` first ({exc})", file=sys.stderr)
        return 1

    badge = {
        "schemaVersion": 1,
        "label": "coverage",
        "message": f"{pct:.0f}%",
        "color": color_for(pct),
    }
    BADGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BADGE_PATH.write_text(json.dumps(badge, indent=2) + "\n", encoding="utf-8")
    print(f"gen_coverage_badge: wrote {BADGE_PATH} ({pct:.0f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
