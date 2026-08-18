"""Semantic-aware chunking with overlap and denormalized retrieval metadata.

Chunking is the last ingestion stage before indexing, and one invariant makes every downstream
citation checkable: for every emitted chunk,
``document.text[chunk.start_char:chunk.end_char] == chunk.text``. The chunker never rewrites
text - it only chooses offsets - so a citation can always be replayed against the stored
document.

The strategy, in order of precedence:

1. **Sections bound chunks.** Each :class:`~regulon.ingestion.models.Section` is packed on its
   own, so no chunk mixes two sections. A section shorter than ``min_chars`` is merged forward
   into the next one instead of becoming a stub chunk of its own.
2. **Packing prefers structure.** Within a section, text is packed up to ``max_chars``, breaking
   at the last paragraph boundary that fits, else the last sentence boundary, else the last word
   boundary. Only a single word longer than ``max_chars`` is ever split mid-word.
3. **Neighbours overlap.** Consecutive chunks of one section repeat the previous chunk's tail,
   ``overlap_chars`` long and rounded forward to a whole word, so a fact straddling a break stays
   retrievable from either side.

Chunking is deterministic: the same document and the same
:class:`~regulon.core.config.ChunkingSettings` always produce the same chunk list, ids included.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Sequence
from typing import NamedTuple

from regulon.core.config import ChunkingSettings, load_settings
from regulon.ingestion.models import Chunk, ChunkMetadata, NormalizedDocument, Section, make_chunk_id

_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n\s*")
_SENTENCE_BREAK = re.compile(r"[.!?][\"')\]]*\s+")
_WHITESPACE = re.compile(r"\s+")

# Break preference, strongest first: paragraph, then sentence, then any word boundary.
_BREAK_PATTERNS: tuple[re.Pattern[str], ...] = (_PARAGRAPH_BREAK, _SENTENCE_BREAK, _WHITESPACE)


class _Span(NamedTuple):
    """A half-open ``[start, end)`` character range in the document text."""

    start: int
    end: int


class _HeadingIndex(NamedTuple):
    """Lookup from a character offset to the heading of the section covering it."""

    starts: tuple[int, ...]
    sections: tuple[Section, ...]

    def heading_at(self, offset: int) -> str | None:
        """Return the heading of the section covering ``offset``, or None if it covers a gap."""
        index = bisect_right(self.starts, offset) - 1
        if index < 0:
            return None
        section = self.sections[index]
        return section.heading if offset < section.end_char else None


def _last_match_end(text: str, start: int, limit: int, pattern: re.Pattern[str]) -> int | None:
    """Return the end offset of the last ``pattern`` match inside ``[start, limit)``, if any."""
    end: int | None = None
    for match in pattern.finditer(text, start, limit):
        end = match.end()
    return end


def _word_start(text: str, pos: int, limit: int) -> int:
    """Return ``pos`` moved forward to a word start, or unchanged if none exists below ``limit``."""
    if pos <= 0 or pos >= limit:
        return pos
    if text[pos - 1].isspace() or text[pos].isspace():
        return pos
    match = _WHITESPACE.search(text, pos, limit)
    return match.end() if match is not None else pos


def _trim(text: str, span: _Span) -> _Span:
    """Return ``span`` with leading and trailing whitespace excluded, offsets adjusted to match."""
    start, end = span
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return _Span(start, end)


def _section_spans(sections: Sequence[Section], text_len: int) -> list[_Span]:
    """Return every span of the document in reading order, including gaps between sections.

    Sections are expected to be sorted, non-overlapping, and within the document text; anything
    else is clipped so the returned spans always tile ``[0, text_len)`` exactly once. Text that
    no section claims becomes its own span, so no character is silently dropped.

    Args:
        sections: Sections sorted by start offset.
        text_len: Length of the document text.

    Returns:
        Contiguous, non-overlapping spans covering the whole document text.
    """
    spans: list[_Span] = []
    cursor = 0
    for section in sections:
        start = max(section.start_char, cursor)
        end = min(section.end_char, text_len)
        if end <= start:
            continue
        if start > cursor:
            spans.append(_Span(cursor, start))
        spans.append(_Span(start, end))
        cursor = end
    if cursor < text_len:
        spans.append(_Span(cursor, text_len))
    return spans


def _merge_short_spans(spans: Sequence[_Span], min_chars: int) -> list[_Span]:
    """Merge spans shorter than ``min_chars`` forward into their successor.

    A run of short spans accumulates until the accumulated span reaches ``min_chars``. A short
    span at the very end has no successor, so it merges backward into the preceding unit
    instead - the alternative would be emitting a stub chunk the rule exists to prevent.

    Args:
        spans: Contiguous spans in reading order.
        min_chars: Minimum length a span must reach to stand on its own.

    Returns:
        Contiguous packing units, still covering exactly the input range.
    """
    merged: list[_Span] = []
    pending: int | None = None
    for span in spans:
        start = span.start if pending is None else pending
        if span.end - start < min_chars:
            pending = start
            continue
        merged.append(_Span(start, span.end))
        pending = None
    if pending is not None:
        tail_end = spans[-1].end
        if merged:
            merged[-1] = _Span(merged[-1].start, tail_end)
        else:
            merged.append(_Span(pending, tail_end))
    return merged


def _drop_contained(spans: Sequence[_Span]) -> list[_Span]:
    """Drop spans wholly covered by a neighbour, keeping the widest of each nested group.

    Trimming whitespace off two different windows can leave one span inside another - only
    reachable when the overlap is a large fraction of the window - and the narrower one would be
    a chunk whose every character another chunk already carries.

    Args:
        spans: Trimmed, non-empty spans in emission order.

    Returns:
        The same spans minus those contained in a kept neighbour.
    """
    kept: list[_Span] = []
    for span in spans:
        while kept and span.start <= kept[-1].start and span.end >= kept[-1].end:
            kept.pop()
        if kept and span.start >= kept[-1].start and span.end <= kept[-1].end:
            continue
        kept.append(span)
    return kept


class Chunker:
    """Splits a normalized document into overlapping, section-aware, citable chunks.

    The chunker holds no per-document state, so one instance can chunk any number of documents
    and two instances built from equal settings always agree.
    """

    def __init__(self, settings: ChunkingSettings | None = None) -> None:
        """Build a chunker.

        Args:
            settings: Chunking bounds. Defaults to ``ingestion.chunking`` from the loaded Regulon
                configuration, so callers never hardcode sizes.
        """
        self._settings = settings if settings is not None else load_settings().ingestion.chunking
        self._max_chars = self._settings.max_chars
        # An overlap of max_chars or more would re-read the window it just consumed, so the packer
        # would never advance. One character below the window is the largest value that always
        # makes progress; it is a safety clamp, not a tunable.
        self._overlap = max(min(self._settings.overlap_chars, self._max_chars - 1), 0)
        # A structural break closer than this to the chunk start is rejected in favour of a weaker
        # but better-filled one, which keeps the chunker from emitting stubs it would refuse to
        # emit as a section. Clamped so it can never exceed the window.
        self._min_fill = min(self._settings.min_chars, self._max_chars)

    def chunk(self, document: NormalizedDocument) -> list[Chunk]:
        """Split a document into chunks.

        Args:
            document: Normalized document to split.

        Returns:
            Chunks in reading order with sequential zero-based ``chunk_index`` values. Each chunk
            carries half-open offsets satisfying ``document.text[start_char:end_char] == text``,
            covers every non-whitespace character of the document, and is at most ``max_chars``
            long. An empty or whitespace-only document yields an empty list.
        """
        text = document.text
        sections = tuple(sorted(document.sections, key=lambda section: (section.start_char, section.end_char)))
        headings = _HeadingIndex(tuple(section.start_char for section in sections), sections)
        units = _merge_short_spans(_section_spans(sections, len(text)), self._settings.min_chars)

        windows: list[_Span] = []
        for unit in units:
            for window in self._pack(text, unit):
                span = _trim(text, window)
                if span.start < span.end:
                    windows.append(span)
        return [
            self._build_chunk(document, headings, span, index) for index, span in enumerate(_drop_contained(windows))
        ]

    def _pack(self, text: str, unit: _Span) -> list[_Span]:
        """Return the chunk windows for one packing unit, in order and overlapping."""
        spans: list[_Span] = []
        pos = unit.start
        while True:
            if unit.end - pos <= self._max_chars:
                spans.append(_Span(pos, unit.end))
                return spans
            stop = self._break_point(text, pos, pos + self._max_chars)
            spans.append(_Span(pos, stop))
            # Never carry back more than the chunk minus one character: the next window must
            # start strictly after this one, or the loop would not terminate.
            overlap = min(self._overlap, stop - pos - 1)
            pos = _word_start(text, stop - overlap, stop)

    def _break_point(self, text: str, start: int, limit: int) -> int:
        """Return where to end a chunk that starts at ``start`` and may not extend past ``limit``.

        The strongest structural break that both fits the window and leaves a chunk of at least
        ``min_chars`` wins. When no break qualifies - a single word longer than the window - the
        window edge is used, splitting mid-word rather than looping forever.
        """
        floor = start + self._min_fill
        for pattern in _BREAK_PATTERNS:
            candidate = _last_match_end(text, start, limit, pattern)
            if candidate is not None and candidate >= floor:
                return candidate
        return limit

    def _build_chunk(self, document: NormalizedDocument, headings: _HeadingIndex, span: _Span, index: int) -> Chunk:
        """Build one chunk from a span, denormalizing document metadata onto it."""
        text = document.text[span.start : span.end]
        source = document.metadata
        metadata = ChunkMetadata(
            document_id=document.document_id,
            source_path=source.source_path,
            chunk_index=index,
            section_heading=headings.heading_at(span.start),
            ticker=source.ticker,
            form=source.form,
            fiscal_year=source.fiscal_year,
            is_synthetic=source.is_synthetic,
        )
        return Chunk(
            chunk_id=make_chunk_id(document.document_id, index, text),
            document_id=document.document_id,
            text=text,
            start_char=span.start,
            end_char=span.end,
            metadata=metadata,
        )
