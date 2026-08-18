"""Ingestion subsystem: parse, normalize, redact, chunk, and store source documents.

This package's public contract - the models every stage exchanges, the deterministic id
builders, and the error hierarchy - is re-exported here, so downstream subsystems import from
``regulon.ingestion`` rather than reaching into individual modules.
"""

from __future__ import annotations

from regulon.ingestion.errors import (
    DocumentParseError,
    EdgarError,
    IngestionError,
    StoreError,
    UnsupportedSourceError,
)
from regulon.ingestion.models import (
    REDACTION_KINDS,
    Chunk,
    ChunkMetadata,
    DocumentIngestSummary,
    DocumentMetadata,
    IngestReport,
    NormalizedDocument,
    RedactionEvent,
    RedactionResult,
    Section,
    SourceKind,
    make_chunk_id,
    make_document_id,
)

__all__ = [
    "REDACTION_KINDS",
    "Chunk",
    "ChunkMetadata",
    "DocumentIngestSummary",
    "DocumentMetadata",
    "DocumentParseError",
    "EdgarError",
    "IngestReport",
    "IngestionError",
    "NormalizedDocument",
    "RedactionEvent",
    "RedactionResult",
    "Section",
    "SourceKind",
    "StoreError",
    "UnsupportedSourceError",
    "make_chunk_id",
    "make_document_id",
]
