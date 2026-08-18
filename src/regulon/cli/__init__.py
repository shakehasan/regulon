"""Command-line interface for Regulon.

The Typer application is defined in :mod:`regulon.cli.main` and re-exported here, so both
``regulon.cli:app`` and the packaged ``regulon`` console script reach the same object.

The re-export is resolved lazily (:pep:`562`). Importing the submodule eagerly would put
``regulon.cli.main`` in ``sys.modules`` before ``python -m regulon.cli.main`` got to execute it,
which makes the interpreter warn about a double import and hand the two copies separate
application objects.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # Re-declared for type checkers, which cannot follow the lazy lookup below.
    from regulon.cli.main import app

__all__ = ["app"]


def __getattr__(name: str) -> object:
    """Resolve :data:`app` on first access instead of at import time.

    Args:
        name: Attribute requested from this package.

    Returns:
        The Typer application when ``name`` is ``"app"``.

    Raises:
        AttributeError: If ``name`` is anything else.
    """
    if name == "app":
        from regulon.cli.main import app as application

        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
