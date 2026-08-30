"""Module entry point so ``python -m quorum.cli`` behaves like the ``quorum`` console script.

The packaged script is the documented way in, but it only exists on ``PATH`` once the
environment is activated. Running the package as a module works from any checkout with the
project installed, which is what a reader following the README from a fresh clone tends to
reach for.
"""

from __future__ import annotations

from quorum.cli.main import app

if __name__ == "__main__":  # pragma: no cover - covered by a subprocess test, not by the importing run
    app()
