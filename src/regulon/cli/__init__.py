"""Command-line interface for Regulon.

The Typer application is defined in :mod:`regulon.cli.main` and re-exported here, so both
``regulon.cli:app`` and the packaged ``regulon`` console script reach the same object.
"""

from __future__ import annotations

from regulon.cli.main import app

__all__ = ["app"]
