"""Pydantic contract shared by every stage of the ingestion pipeline.

Ingestion runs ``parse -> normalize -> redact -> chunk -> store``, and the stages exchange only
the models defined here: a :class:`NormalizedDocument` carries cleaned text plus its
:class:`Section` map, a :class:`Chunk` carries one slice of that text with its
:class:`ChunkMetadata`, and an :class:`IngestReport` summarizes a whole run.

Two conventions bind every implementation:

* **Offsets** are character offsets into :attr:`NormalizedDocument.text`, half-open
  ``[start_char, end_char)``, so ``text[start_char:end_char]`` is the covered span.
* **Identifiers** are deterministic content hashes (:func:`make_document_id`,
  :func:`make_chunk_id`): identical input always yields an identical id, on any machine and in
  any run, so ingestion is replayable and stores can deduplicate by id alone.

Every model is frozen and rejects unknown fields, so a contract drift shows up as a loud
validation error rather than a silently dropped attribute.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from quorum.core.hashing import hash_json

_DOCUMENT_ID_PREFIX = "doc_"
_CHUNK_ID_PREFIX = "chk_"
# Width of the digest slice carried by every id. Not a tunable: changing it changes every id
# ever written to the store, so it is pinned here next to the id builders.
_ID_HASH_CHARS = 16

REDACTION_KINDS: frozenset[str] = frozenset({"email", "phone", "ssn"})
"""Redaction categories the ingest-time redactor emits, used as :attr:`RedactionEvent.kind`."""


class SourceKind(StrEnum):
    """Source format a document was read from."""

    TEXT = "text"
    MARKDOWN = "markdown"
    HTML = "html"
    PDF = "pdf"


def _check_span(start_char: int, end_char: int) -> None:
    """Validate a half-open character span.

    Args:
        start_char: Inclusive start offset.
        end_char: Exclusive end offset.

    Raises:
        ValueError: If the span ends before it starts.
    """
    if end_char < start_char:
        raise ValueError(f"end_char ({end_char}) must not precede start_char ({start_char})")


class DocumentMetadata(BaseModel):
    """Provenance and filing attributes attached to a source document.

    Attributes:
        source_path: Path or URL the document was read from; the provenance anchor for citations.
        title: Human-readable document title, when the source states one.
        ticker: Issuer ticker symbol for filings.
        form: Filing form type, e.g. ``10-K``.
        fiscal_year: Fiscal year the document reports on.
        is_synthetic: True for generated corpus documents, which are labeled as such everywhere.
        extra: Free-form string attributes a source supplies that the fields above do not cover.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_path: str = Field(min_length=1)
    title: str | None = None
    ticker: str | None = None
    form: str | None = None
    fiscal_year: int | None = None
    is_synthetic: bool = False
    extra: dict[str, str] = Field(default_factory=dict)


class Section(BaseModel):
    """One contiguous region of a normalized document, usually a heading and its body.

    Attributes:
        order: Zero-based position of the section in reading order.
        heading: Heading text, or None for text that precedes the first heading.
        text: The section text, equal to the document text over ``[start_char, end_char)``.
        start_char: Inclusive start offset into :attr:`NormalizedDocument.text`.
        end_char: Exclusive end offset into :attr:`NormalizedDocument.text`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    order: int = Field(ge=0)
    heading: str | None
    text: str
    start_char: int = Field(ge=0)
    end_char: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_span(self) -> Self:
        _check_span(self.start_char, self.end_char)
        return self


class NormalizedDocument(BaseModel):
    """A parsed, normalized document: the single input the chunker works from.

    Attributes:
        document_id: Deterministic content id from :func:`make_document_id`.
        kind: Format the document was parsed from.
        text: Normalized full text; all offsets in this contract index into it.
        sections: Sections in reading order, covering non-overlapping spans of :attr:`text`.
        metadata: Provenance of the source document.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str = Field(min_length=1)
    kind: SourceKind
    text: str
    sections: list[Section]
    metadata: DocumentMetadata


class ChunkMetadata(BaseModel):
    """Retrieval-time metadata denormalized onto every chunk.

    The document-level fields are copied here on purpose: retrieval filters and citation
    rendering must work from a chunk alone, without a join back to the document.

    Attributes:
        document_id: Id of the document the chunk came from.
        source_path: Path or URL of the source document, for citation display.
        chunk_index: Zero-based position of the chunk within its document.
        section_heading: Heading of the section the chunk starts in, when known.
        ticker: Issuer ticker symbol, copied from the document metadata.
        form: Filing form type, copied from the document metadata.
        fiscal_year: Fiscal year, copied from the document metadata.
        is_synthetic: True when the chunk comes from the synthetic corpus.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    chunk_index: int = Field(ge=0)
    section_heading: str | None = None
    ticker: str | None = None
    form: str | None = None
    fiscal_year: int | None = None
    is_synthetic: bool = False


class Chunk(BaseModel):
    """One retrievable unit of text with its provenance.

    Attributes:
        chunk_id: Deterministic content id from :func:`make_chunk_id`.
        document_id: Id of the document the chunk came from.
        text: The chunk text.
        start_char: Inclusive start offset into :attr:`NormalizedDocument.text`.
        end_char: Exclusive end offset into :attr:`NormalizedDocument.text`.
        metadata: Denormalized retrieval metadata.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    chunk_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    text: str
    start_char: int = Field(ge=0)
    end_char: int = Field(ge=0)
    metadata: ChunkMetadata

    @model_validator(mode="after")
    def _validate_span(self) -> Self:
        _check_span(self.start_char, self.end_char)
        return self


class RedactionEvent(BaseModel):
    """A single replacement the redactor made, recorded for the audit trail.

    Offsets refer to the text *before* the replacement was applied, so a redaction pass is
    auditable without storing the original sensitive text.

    Attributes:
        kind: Category that matched; see :data:`REDACTION_KINDS`.
        start_char: Inclusive start offset of the matched span in the pre-redaction text.
        end_char: Exclusive end offset of the matched span in the pre-redaction text.
        replacement: Placeholder written in place of the matched span.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str = Field(min_length=1)
    start_char: int = Field(ge=0)
    end_char: int = Field(ge=0)
    replacement: str

    @model_validator(mode="after")
    def _validate_span(self) -> Self:
        _check_span(self.start_char, self.end_char)
        return self


class RedactionResult(BaseModel):
    """Outcome of redacting one piece of text.

    Attributes:
        text: The redacted text.
        events: Every replacement made, in ascending order of :attr:`RedactionEvent.start_char`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    events: tuple[RedactionEvent, ...] = ()


class DocumentIngestSummary(BaseModel):
    """What ingestion produced for one document.

    Attributes:
        document_id: Deterministic id of the ingested document.
        source_path: Path or URL the document was read from.
        chunks: Number of chunks written for the document.
        redactions: Number of redaction events applied to the document.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    chunks: int = Field(ge=0)
    redactions: int = Field(ge=0)


class IngestReport(BaseModel):
    """Result of one ingestion run, as reported by the CLI and stored with the run.

    The counters are stated explicitly rather than derived so a caller can report a partial
    run; in a complete run ``documents_ingested`` equals ``len(documents)``, ``chunks_created``
    the sum of per-document chunks, and ``redactions_applied`` the sum of per-document
    redactions.

    Attributes:
        documents_ingested: Number of documents written.
        chunks_created: Number of chunks written across all documents.
        redactions_applied: Number of redaction events applied across all documents.
        documents: Per-document summaries, in ingestion order.
        skipped: Source paths that were not ingested, e.g. unsupported or unparseable inputs.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    documents_ingested: int = Field(ge=0)
    chunks_created: int = Field(ge=0)
    redactions_applied: int = Field(ge=0)
    documents: tuple[DocumentIngestSummary, ...] = ()
    skipped: tuple[str, ...] = ()


def make_document_id(text: str, source_path: str) -> str:
    """Return the deterministic id of a document.

    The id is a namespaced SHA-256 digest over the canonical JSON of the document's text and
    source path, so the same file ingested twice yields the same id, and two documents differ
    in id whenever either their text or their origin differs.

    Args:
        text: Normalized document text.
        source_path: Path or URL the document was read from.

    Returns:
        An id of the form ``doc_<16 hex chars>``.
    """
    digest = hash_json({"ns": "document", "source_path": source_path, "text": text})
    return f"{_DOCUMENT_ID_PREFIX}{digest[:_ID_HASH_CHARS]}"


def make_chunk_id(document_id: str, chunk_index: int, text: str) -> str:
    """Return the deterministic id of a chunk.

    The chunk index participates in the digest so that repeated text within one document -
    boilerplate, repeated table headers - still yields distinct chunk ids.

    Args:
        document_id: Id of the document the chunk came from.
        chunk_index: Zero-based position of the chunk within that document.
        text: The chunk text.

    Returns:
        An id of the form ``chk_<16 hex chars>``.
    """
    digest = hash_json({"ns": "chunk", "document_id": document_id, "chunk_index": chunk_index, "text": text})
    return f"{_CHUNK_ID_PREFIX}{digest[:_ID_HASH_CHARS]}"
