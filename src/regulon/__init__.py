"""Regulon: governed multi-agent RAG platform."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("regulon")
except PackageNotFoundError:  # pragma: no cover - only hit when not installed
    __version__ = "0.0.0"

__all__ = ["__version__"]
