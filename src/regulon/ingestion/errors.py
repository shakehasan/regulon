"""Exceptions raised by the ingestion subsystem.

Every error here derives from :class:`~regulon.core.errors.RegulonError` through
:class:`IngestionError`, so a caller can catch the whole subsystem with one except clause
while still distinguishing an unsupported input from a fetch failure.
"""

from __future__ import annotations

from regulon.core.errors import RegulonError


class IngestionError(RegulonError):
    """Base class for every ingestion failure."""


class UnsupportedSourceError(IngestionError):
    """Raised when a source has no parser, e.g. an unknown file extension."""


class DocumentParseError(IngestionError):
    """Raised when a supported source cannot be read or produces no usable text."""


class EdgarError(IngestionError):
    """Raised when a filings fetch fails: transport error, bad status, or malformed response."""


class StoreError(IngestionError):
    """Raised when the document store cannot be opened, written, or read."""
