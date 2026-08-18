"""End-to-end ingestion: load, redact, chunk, and store a file or a whole corpus.

This module is the only place the four ingestion stages meet, and it fixes their order:

1. :func:`~regulon.ingestion.loaders.load_document` parses and normalizes the source.
2. :class:`~regulon.ingestion.redaction.Redactor` removes PII from the normalized text.
3. :class:`~regulon.ingestion.chunking.Chunker` splits the **redacted** text.
4. :class:`~regulon.ingestion.store.ChunkStore` persists the document and its chunks.

**Redaction runs before chunking, and before anything is written.** No un-redacted text ever
reaches the knowledge base, not even transiently, because redaction happens in memory between
parsing and the first store call. The cost of that ordering is an offset shift: a placeholder is
rarely the same length as the text it replaces, so every character offset in a stored document -
:class:`~regulon.ingestion.models.Section` bounds, :attr:`~regulon.ingestion.models.Chunk.start_char`
and :attr:`~regulon.ingestion.models.Chunk.end_char` - indexes the **redacted** text that was
stored, never the original file. The pipeline maintains that by remapping section offsets through
the redaction events before chunking, so the contract invariant
``stored_document.text[chunk.start_char:chunk.end_char] == chunk.text`` holds for every stored
chunk. Redaction event offsets, which index the pre-redaction text by contract, are counted here
and not persisted; schema version 1 stores no audit trail.

For the same reason the stored ``document_id`` is recomputed from the redacted text: an id is a
hash of the content it identifies, so a consumer can always re-derive a stored document's id from
what the store returned. A file re-ingested after a change that redaction removes anyway - a new
contact address in the same filing - therefore keeps its id and updates in place, rather than
accumulating a second copy of text the store cannot tell apart.

Batch ingestion is fault-tolerant and deterministic. Every non-hidden file under a directory is
attempted in sorted order; a file with no parser, or one that fails to parse, is recorded in
:attr:`~regulon.ingestion.models.IngestReport.skipped` with its reason and the run continues.
Unlike :func:`~regulon.ingestion.loaders.iter_source_files`, which filters unsupported extensions
out silently, the pipeline reports them: a user who points at a directory should be told which of
its files did not make it into the knowledge base. A
:class:`~regulon.ingestion.errors.StoreError` is not caught - a knowledge base that cannot be
written cannot produce a meaningful report - so it aborts the run.
"""

from __future__ import annotations

import logging
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from regulon.core.config import Settings, load_settings
from regulon.ingestion.chunking import Chunker
from regulon.ingestion.errors import DocumentParseError, UnsupportedSourceError
from regulon.ingestion.loaders import load_document
from regulon.ingestion.models import (
    DocumentIngestSummary,
    IngestReport,
    NormalizedDocument,
    RedactionEvent,
    Section,
    make_document_id,
)
from regulon.ingestion.redaction import Redactor
from regulon.ingestion.store import ChunkStore

__all__ = ["IngestionPipeline"]

_LOGGER = logging.getLogger(__name__)
# Library convention: attach a null handler so a skipped file never prints itself through
# logging's last-resort handler on top of the report the caller already renders.
_LOGGER.addHandler(logging.NullHandler())


@dataclass(frozen=True, slots=True)
class _OffsetMap:
    """Translation from pre-redaction character offsets to post-redaction ones.

    The four tuples are parallel and ordered by ``starts``: entry ``i`` describes one replacement,
    covering ``[starts[i], ends[i])`` of the original text and ``[new_starts[i], new_ends[i])`` of
    the redacted text.

    Attributes:
        starts: Inclusive start offsets of the replaced spans, in the original text.
        ends: Exclusive end offsets of the replaced spans, in the original text.
        new_starts: Inclusive start offsets of the placeholders, in the redacted text.
        new_ends: Exclusive end offsets of the placeholders, in the redacted text.
    """

    starts: tuple[int, ...]
    ends: tuple[int, ...]
    new_starts: tuple[int, ...]
    new_ends: tuple[int, ...]

    def map_offset(self, offset: int) -> int:
        """Return where ``offset`` in the original text lands in the redacted text.

        The mapping is exact for any offset outside a replaced span and non-decreasing
        everywhere, so remapped spans stay ordered and non-overlapping. An offset *inside* a
        replaced span collapses to the start of its placeholder, which is the only answer that
        does not point into text that no longer exists.

        Args:
            offset: Character offset into the pre-redaction text.

        Returns:
            The corresponding character offset into the redacted text.
        """
        index = bisect_right(self.starts, offset) - 1
        if index < 0:
            return offset
        if offset >= self.ends[index]:
            return self.new_ends[index] + (offset - self.ends[index])
        return self.new_starts[index]


def _build_offset_map(events: Sequence[RedactionEvent]) -> _OffsetMap:
    """Build the offset translation implied by a redaction pass.

    Args:
        events: Replacements in ascending, non-overlapping order, as the redactor emits them.

    Returns:
        A map from pre-redaction to post-redaction offsets.
    """
    starts: list[int] = []
    ends: list[int] = []
    new_starts: list[int] = []
    new_ends: list[int] = []
    shift = 0
    for event in events:
        new_start = event.start_char + shift
        new_end = new_start + len(event.replacement)
        starts.append(event.start_char)
        ends.append(event.end_char)
        new_starts.append(new_start)
        new_ends.append(new_end)
        shift += len(event.replacement) - (event.end_char - event.start_char)
    return _OffsetMap(tuple(starts), tuple(ends), tuple(new_starts), tuple(new_ends))


def _remap_sections(sections: Sequence[Section], text: str, offsets: _OffsetMap) -> list[Section]:
    """Move a section map onto redacted text, re-slicing each section from the new offsets.

    Args:
        sections: Sections indexing the pre-redaction text.
        text: The redacted text.
        offsets: Translation produced by :func:`_build_offset_map`.

    Returns:
        Sections indexing ``text``, still contiguous and in reading order.
    """
    remapped: list[Section] = []
    for section in sections:
        start = offsets.map_offset(section.start_char)
        end = offsets.map_offset(section.end_char)
        remapped.append(
            Section(order=section.order, heading=section.heading, text=text[start:end], start_char=start, end_char=end)
        )
    return remapped


def _discover(root: Path) -> list[Path]:
    """Return every candidate file under ``root``, in a deterministic order.

    Dot-prefixed files and directories are skipped, so a corpus directory can carry a ``.git``
    or editor state without it reaching the knowledge base. Unsupported extensions are *kept*:
    the pipeline reports them as skipped rather than hiding them.

    Args:
        root: A file, or a directory to walk recursively.

    Returns:
        Files sorted by POSIX path; a single-element list for a file; an empty list when
        ``root`` does not exist.
    """
    if root.is_file():
        return [root]
    if not root.is_dir():
        return []
    found = [
        path
        for path in root.rglob("*")
        if path.is_file() and not any(part.startswith(".") for part in path.relative_to(root).parts)
    ]
    return sorted(found, key=lambda path: path.as_posix())


def _skip_entry(path: Path, reason: str) -> str:
    """Render one ``skipped`` entry as ``<posix path>: <reason>``.

    Loader errors already name the file they concern, so the path is not repeated when the
    reason opens with it.

    Args:
        path: File that was not ingested.
        reason: Why it was not ingested, usually an exception message.

    Returns:
        A single-line, human-readable explanation.
    """
    prefix = f"{path.as_posix()}: "
    detail = reason[len(prefix) :] if reason.startswith(prefix) else reason
    return f"{prefix}{detail}"


class IngestionPipeline:
    """Runs documents through load, redact, chunk, and store, in that order.

    The pipeline holds no per-run state, so one instance can ingest any number of paths, and two
    instances built from equal settings produce identical ids, chunks, and reports.
    """

    def __init__(self, store: ChunkStore, *, settings: Settings | None = None) -> None:
        """Build a pipeline writing to ``store``.

        Args:
            store: Knowledge base to persist documents and chunks into. The pipeline depends on
                the protocol only, so a non-SQLite store drops in without a pipeline change.
            settings: Regulon configuration. Defaults to the loaded settings, so chunk sizes and
                the redaction placeholder stay in ``config/regulon.yaml``.
        """
        self._store = store
        self._settings = settings if settings is not None else load_settings()
        self._redactor = Redactor(self._settings.ingestion.redaction)
        self._chunker = Chunker(self._settings.ingestion.chunking)

    def ingest_path(self, path: Path) -> IngestReport:
        """Ingest one file, or every file under one directory.

        Directories are walked recursively in sorted order, so the same corpus always ingests in
        the same sequence. A source that cannot be parsed does not abort the run: it is recorded
        in the returned report's ``skipped`` list with its reason, and ingestion continues with
        the next file.

        Args:
            path: File or directory to ingest.

        Returns:
            A report aggregating every document ingested, chunk written, and redaction applied,
            plus one ``skipped`` entry per source that was not ingested.

        Raises:
            StoreError: If the knowledge base cannot be written, which aborts the run.
        """
        summaries: list[DocumentIngestSummary] = []
        skipped: list[str] = []
        candidates = _discover(path)
        if not candidates and not path.exists():
            skipped.append(_skip_entry(path, "no such file or directory"))
        for candidate in candidates:
            try:
                summaries.append(self.ingest_document(candidate))
            except (UnsupportedSourceError, DocumentParseError) as exc:
                _LOGGER.warning("skipping %s: %s", candidate.as_posix(), exc)
                skipped.append(_skip_entry(candidate, str(exc)))
        return IngestReport(
            documents_ingested=len(summaries),
            chunks_created=sum(summary.chunks for summary in summaries),
            redactions_applied=sum(summary.redactions for summary in summaries),
            documents=tuple(summaries),
            skipped=tuple(skipped),
        )

    def ingest_document(self, path: Path) -> DocumentIngestSummary:
        """Ingest exactly one document, writing it and its chunks to the store.

        Args:
            path: File to ingest.

        Returns:
            What the document contributed to the run: its id, its source path, and how many
            chunks and redactions it produced.

        Raises:
            UnsupportedSourceError: If the file extension has no parser.
            DocumentParseError: If the file cannot be read or parsed.
            StoreError: If the document or its chunks cannot be written.
        """
        document = load_document(path)
        redacted, redactions = self._redact(document)
        chunks = self._chunker.chunk(redacted)
        # Document first: the store enforces the chunk -> document foreign key.
        self._store.add_document(redacted)
        self._store.add_chunks(chunks)
        return DocumentIngestSummary(
            document_id=redacted.document_id,
            source_path=redacted.metadata.source_path,
            chunks=len(chunks),
            redactions=redactions,
        )

    def _redact(self, document: NormalizedDocument) -> tuple[NormalizedDocument, int]:
        """Return the document with PII replaced, plus how many replacements were made.

        The returned document carries the redacted text, a section map remapped onto it, and an
        id recomputed from it, so every offset and identifier the store receives describes the
        text the store actually holds. A pass that changed nothing returns the input untouched.

        Args:
            document: Normalized document straight from the loader.

        Returns:
            The redacted document and its redaction count.
        """
        result = self._redactor.redact(document.text)
        if not result.events:
            return document, 0
        offsets = _build_offset_map(result.events)
        return (
            NormalizedDocument(
                document_id=make_document_id(result.text, document.metadata.source_path),
                kind=document.kind,
                text=result.text,
                sections=_remap_sections(document.sections, result.text, offsets),
                metadata=document.metadata,
            ),
            len(result.events),
        )
