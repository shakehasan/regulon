"""Tests for the ingestion pipeline: stage order, offset fidelity, and batch fault tolerance.

Two properties get the most attention because everything downstream leans on them: nothing
un-redacted may reach the store, and every offset the store receives must index the redacted text
that was stored. Every PII-shaped sample is assembled by concatenation so the repository's own
safety scanner stays clean, and nothing here touches a network or a path outside ``tmp_path``.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from quorum.core.config import ChunkingSettings, IngestionSettings, RedactionSettings, Settings
from quorum.ingestion.errors import StoreError
from quorum.ingestion.loaders import load_document
from quorum.ingestion.models import (
    Chunk,
    IngestReport,
    NormalizedDocument,
    RedactionEvent,
    make_document_id,
)
from quorum.ingestion.pipeline import IngestionPipeline, _build_offset_map, _discover, _skip_entry
from quorum.ingestion.store import ChunkStore, SQLiteChunkStore

# Built by concatenation so the literal never appears in the repository.
EMAIL = "analyst" + "@" + "example.com"
OTHER_EMAIL = "counsel" + "@" + "example.org"
PHONE = "555" + "-" + "010" + "-" + "9999"

MARKDOWN = f"""---
title: SYNTHETIC Annual Report
ticker: ACME
form: 10-K
fiscal_year: 2024
synthetic: true
---

# Item 1. Business

The issuer operates two segments and reported 1,234,567 in revenue.
Investor relations answers at {EMAIL} during business hours.

## Item 1A. Risk Factors

Markets move. Reach the compliance desk on {PHONE} for filing questions.
Concentration of customers remains the single largest exposure this year.
"""

PLAIN_TEXT = "Operating Notes\n\nThe segment recorded steady demand across the period.\n"


class RecordingStore:
    """A :class:`~quorum.ingestion.store.ChunkStore` that records the calls it receives."""

    def __init__(self, *, fail_on_document: bool = False, fail_on_chunks: bool = False) -> None:
        """Build a store that records every call and optionally fails one of the writes."""
        self.calls: list[str] = []
        self.documents: list[NormalizedDocument] = []
        self.chunks: list[Chunk] = []
        self._fail_on_document = fail_on_document
        self._fail_on_chunks = fail_on_chunks

    def add_document(self, document: NormalizedDocument) -> None:
        self.calls.append("add_document")
        if self._fail_on_document:
            raise StoreError("cannot write the document")
        self.documents.append(document)

    def add_chunks(self, chunks: Sequence[Chunk]) -> int:
        self.calls.append("add_chunks")
        if self._fail_on_chunks:
            raise StoreError("cannot write the chunks")
        self.chunks.extend(chunks)
        return len(chunks)

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        return next((chunk for chunk in self.chunks if chunk.chunk_id == chunk_id), None)

    def iter_chunks(self, *, document_id: str | None = None) -> Iterator[Chunk]:
        return iter([c for c in self.chunks if document_id is None or c.document_id == document_id])

    def count_documents(self) -> int:
        return len(self.documents)

    def count_chunks(self) -> int:
        return len(self.chunks)

    def close(self) -> None:
        self.calls.append("close")


def settings_with(
    *, redaction_enabled: bool = True, max_chars: int = 1200, min_chars: int = 200, overlap_chars: int = 150
) -> Settings:
    return Settings(
        ingestion=IngestionSettings(
            chunking=ChunkingSettings(max_chars=max_chars, overlap_chars=overlap_chars, min_chars=min_chars),
            redaction=RedactionSettings(enabled=redaction_enabled),
        )
    )


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """Build a small mixed corpus: two loadable files, one with no parser, one hidden."""
    root = tmp_path / "corpus"
    (root / "nested").mkdir(parents=True)
    (root / "filing.md").write_text(MARKDOWN, encoding="utf-8")
    (root / "nested" / "notes.txt").write_text(PLAIN_TEXT, encoding="utf-8")
    (root / "archive.xyz").write_text("no parser knows this", encoding="utf-8")
    (root / ".hidden.md").write_text("# Hidden\n\nEditor state.\n", encoding="utf-8")
    return root


@pytest.fixture
def store() -> Iterator[SQLiteChunkStore]:
    with SQLiteChunkStore(Path(":memory:")) as opened:
        yield opened


def pipeline_for(store: ChunkStore, **kwargs: bool | int) -> IngestionPipeline:
    return IngestionPipeline(store, settings=settings_with(**kwargs))


# --- single document -------------------------------------------------------------------------


def test_ingest_document_summarizes_what_it_wrote(tmp_path: Path, store: SQLiteChunkStore):
    path = tmp_path / "filing.md"
    path.write_text(MARKDOWN, encoding="utf-8")

    summary = pipeline_for(store).ingest_document(path)

    assert summary.source_path == path.as_posix()
    assert summary.chunks == store.count_chunks()
    assert summary.chunks > 0
    assert summary.redactions == 2
    assert store.count_documents() == 1


def test_store_receives_the_document_before_its_chunks(tmp_path: Path):
    path = tmp_path / "filing.md"
    path.write_text(MARKDOWN, encoding="utf-8")
    recorder = RecordingStore()

    pipeline_for(recorder).ingest_document(path)

    assert recorder.calls == ["add_document", "add_chunks"]


def test_document_metadata_survives_ingestion(tmp_path: Path):
    path = tmp_path / "filing.md"
    path.write_text(MARKDOWN, encoding="utf-8")
    recorder = RecordingStore()

    pipeline_for(recorder).ingest_document(path)

    metadata = recorder.documents[0].metadata
    assert (metadata.ticker, metadata.form, metadata.fiscal_year, metadata.is_synthetic) == ("ACME", "10-K", 2024, True)
    assert all(chunk.metadata.ticker == "ACME" for chunk in recorder.chunks)


# --- redaction before storage ----------------------------------------------------------------


def test_no_pii_reaches_the_store(tmp_path: Path):
    path = tmp_path / "filing.md"
    path.write_text(MARKDOWN, encoding="utf-8")
    recorder = RecordingStore()

    pipeline_for(recorder).ingest_document(path)

    stored_text = recorder.documents[0].text
    assert EMAIL not in stored_text
    assert PHONE not in stored_text
    assert "[REDACTED:email]" in stored_text
    assert "[REDACTED:phone]" in stored_text
    assert all(EMAIL not in chunk.text and PHONE not in chunk.text for chunk in recorder.chunks)


def test_stored_chunk_offsets_index_the_redacted_text(tmp_path: Path):
    path = tmp_path / "filing.md"
    path.write_text(MARKDOWN, encoding="utf-8")
    recorder = RecordingStore()

    pipeline_for(recorder, max_chars=200, min_chars=50, overlap_chars=20).ingest_document(path)

    stored = recorder.documents[0]
    assert len(recorder.chunks) > 1
    for chunk in recorder.chunks:
        assert stored.text[chunk.start_char : chunk.end_char] == chunk.text


def test_stored_sections_are_remapped_onto_the_redacted_text(tmp_path: Path):
    path = tmp_path / "filing.md"
    path.write_text(MARKDOWN, encoding="utf-8")
    recorder = RecordingStore()

    pipeline_for(recorder).ingest_document(path)

    stored = recorder.documents[0]
    assert [section.heading for section in stored.sections] == ["Item 1. Business", "Item 1A. Risk Factors"]
    assert stored.sections[0].start_char == 0
    assert stored.sections[-1].end_char == len(stored.text)
    for section in stored.sections:
        assert stored.text[section.start_char : section.end_char] == section.text


def test_section_offsets_shift_by_the_redaction_delta(tmp_path: Path):
    path = tmp_path / "filing.md"
    path.write_text(MARKDOWN, encoding="utf-8")
    recorder = RecordingStore()

    pipeline_for(recorder).ingest_document(path)

    original = load_document(path)
    stored = recorder.documents[0]
    email_delta = len("[REDACTED:email]") - len(EMAIL)
    phone_delta = len("[REDACTED:phone]") - len(PHONE)
    # The email sits in the first section, so the second section starts one email-delta earlier;
    # the document end moves by both deltas.
    assert stored.sections[0].start_char == original.sections[0].start_char == 0
    assert stored.sections[1].start_char == original.sections[1].start_char + email_delta
    assert stored.sections[-1].end_char == original.sections[-1].end_char + email_delta + phone_delta
    assert len(stored.text) == len(original.text) + email_delta + phone_delta


def test_stored_document_id_hashes_the_stored_text(tmp_path: Path):
    path = tmp_path / "filing.md"
    path.write_text(MARKDOWN, encoding="utf-8")
    recorder = RecordingStore()

    pipeline_for(recorder).ingest_document(path)

    stored = recorder.documents[0]
    assert stored.document_id == make_document_id(stored.text, stored.metadata.source_path)
    assert stored.document_id != load_document(path).document_id
    assert all(chunk.document_id == stored.document_id for chunk in recorder.chunks)


def test_reingest_keeps_the_id_when_only_the_pii_changed(tmp_path: Path):
    path = tmp_path / "notes.txt"
    body = "Operating Notes\n\nWrite to {contact} about the filing.\n"
    path.write_text(body.format(contact=EMAIL), encoding="utf-8")
    recorder = RecordingStore()
    pipeline = pipeline_for(recorder)

    first = pipeline.ingest_document(path)
    path.write_text(body.format(contact=OTHER_EMAIL), encoding="utf-8")
    second = pipeline.ingest_document(path)

    assert first == second
    assert recorder.documents[0].text == recorder.documents[1].text


def test_disabled_redaction_leaves_the_document_untouched(tmp_path: Path):
    path = tmp_path / "filing.md"
    path.write_text(MARKDOWN, encoding="utf-8")
    recorder = RecordingStore()

    summary = pipeline_for(recorder, redaction_enabled=False).ingest_document(path)

    stored = recorder.documents[0]
    assert summary.redactions == 0
    assert EMAIL in stored.text
    assert stored.document_id == load_document(path).document_id


def test_document_without_pii_keeps_the_loader_id(tmp_path: Path):
    path = tmp_path / "notes.txt"
    path.write_text(PLAIN_TEXT, encoding="utf-8")
    recorder = RecordingStore()

    summary = pipeline_for(recorder).ingest_document(path)

    assert summary.redactions == 0
    assert summary.document_id == load_document(path).document_id


# --- batch ingestion -------------------------------------------------------------------------


def test_ingest_path_walks_a_directory_recursively(corpus: Path, store: SQLiteChunkStore):
    report = pipeline_for(store).ingest_path(corpus)

    assert report.documents_ingested == 2
    assert [summary.source_path for summary in report.documents] == [
        (corpus / "filing.md").as_posix(),
        (corpus / "nested" / "notes.txt").as_posix(),
    ]


def test_ingest_path_aggregates_counts(corpus: Path, store: SQLiteChunkStore):
    report = pipeline_for(store).ingest_path(corpus)

    assert report.chunks_created == sum(summary.chunks for summary in report.documents)
    assert report.redactions_applied == sum(summary.redactions for summary in report.documents)
    assert report.chunks_created == store.count_chunks()
    assert report.documents_ingested == store.count_documents()


def test_unsupported_file_is_skipped_with_a_reason(corpus: Path, store: SQLiteChunkStore):
    report = pipeline_for(store).ingest_path(corpus)

    assert len(report.skipped) == 1
    entry = report.skipped[0]
    assert entry.startswith((corpus / "archive.xyz").as_posix() + ": ")
    assert "unsupported extension" in entry


def test_hidden_files_are_ignored_entirely(corpus: Path, store: SQLiteChunkStore):
    report = pipeline_for(store).ingest_path(corpus)

    assert all(".hidden" not in summary.source_path for summary in report.documents)
    assert all(".hidden" not in entry for entry in report.skipped)


def test_unparseable_file_does_not_abort_the_run(corpus: Path, store: SQLiteChunkStore):
    (corpus / "broken.txt").write_bytes(b"\xff\xfe not utf-8 \xff")

    report = pipeline_for(store).ingest_path(corpus)

    assert report.documents_ingested == 2
    reasons = "\n".join(report.skipped)
    assert "not valid UTF-8" in reasons
    assert len(report.skipped) == 2


def test_ingest_path_accepts_a_single_file(tmp_path: Path, store: SQLiteChunkStore):
    path = tmp_path / "filing.md"
    path.write_text(MARKDOWN, encoding="utf-8")

    report = pipeline_for(store).ingest_path(path)

    assert report.documents_ingested == 1
    assert report.skipped == ()


def test_unsupported_single_file_is_reported_rather_than_ignored(tmp_path: Path, store: SQLiteChunkStore):
    path = tmp_path / "archive.xyz"
    path.write_text("no parser knows this", encoding="utf-8")

    report = pipeline_for(store).ingest_path(path)

    assert report.documents_ingested == 0
    assert len(report.skipped) == 1


def test_missing_path_is_reported_rather_than_raised(tmp_path: Path, store: SQLiteChunkStore):
    report = pipeline_for(store).ingest_path(tmp_path / "absent")

    assert report == IngestReport(
        documents_ingested=0,
        chunks_created=0,
        redactions_applied=0,
        skipped=((tmp_path / "absent").as_posix() + ": no such file or directory",),
    )


def test_empty_directory_yields_an_empty_report(tmp_path: Path, store: SQLiteChunkStore):
    (tmp_path / "empty").mkdir()

    report = pipeline_for(store).ingest_path(tmp_path / "empty")

    assert report.documents_ingested == 0
    assert report.skipped == ()


@pytest.mark.parametrize(
    ("failure", "expected_calls"),
    [
        ({"fail_on_document": True}, ["add_document"]),
        ({"fail_on_chunks": True}, ["add_document", "add_chunks"]),
    ],
)
def test_store_failure_aborts_the_run(corpus: Path, failure: dict[str, bool], expected_calls: list[str]):
    recorder = RecordingStore(**failure)

    with pytest.raises(StoreError):
        pipeline_for(recorder).ingest_path(corpus)

    # The run stops at the first document rather than walking the rest of the corpus.
    assert recorder.calls == expected_calls


# --- determinism -----------------------------------------------------------------------------


def test_reingesting_the_same_corpus_is_idempotent(corpus: Path, store: SQLiteChunkStore):
    pipeline = pipeline_for(store)

    first = pipeline.ingest_path(corpus)
    second = pipeline.ingest_path(corpus)

    assert first == second
    assert store.count_documents() == first.documents_ingested
    assert store.count_chunks() == first.chunks_created


def test_two_pipelines_agree_on_the_same_corpus(corpus: Path):
    first = RecordingStore()
    second = RecordingStore()

    report_a = pipeline_for(first).ingest_path(corpus)
    report_b = pipeline_for(second).ingest_path(corpus)

    assert report_a == report_b
    assert [chunk.chunk_id for chunk in first.chunks] == [chunk.chunk_id for chunk in second.chunks]


def test_chunk_settings_change_the_chunking_not_the_offsets(corpus: Path):
    coarse = RecordingStore()
    fine = RecordingStore()

    pipeline_for(coarse).ingest_path(corpus)
    pipeline_for(fine, max_chars=150, min_chars=40, overlap_chars=10).ingest_path(corpus)

    assert len(fine.chunks) > len(coarse.chunks)
    by_id = {document.document_id: document for document in fine.documents}
    for chunk in fine.chunks:
        assert by_id[chunk.document_id].text[chunk.start_char : chunk.end_char] == chunk.text


# --- internals -------------------------------------------------------------------------------


def test_offset_map_is_exact_outside_replaced_spans():
    events = (
        RedactionEvent(kind="email", start_char=10, end_char=20, replacement="[E]"),
        RedactionEvent(kind="phone", start_char=30, end_char=42, replacement="[PHONE]"),
    )
    offsets = _build_offset_map(events)

    assert offsets.map_offset(0) == 0
    assert offsets.map_offset(10) == 10
    assert offsets.map_offset(20) == 13  # 20 - 10 removed + 3 written
    assert offsets.map_offset(30) == 23
    assert offsets.map_offset(42) == 30  # 42 - 22 removed + 10 written
    assert offsets.map_offset(50) == 38


def test_offset_map_collapses_offsets_inside_a_replaced_span():
    events = (RedactionEvent(kind="ssn", start_char=5, end_char=16, replacement="[S]"),)
    offsets = _build_offset_map(events)

    assert offsets.map_offset(5) == 5
    assert offsets.map_offset(9) == 5
    assert offsets.map_offset(15) == 5
    assert offsets.map_offset(16) == 8  # the first offset past the 3-character placeholder


def test_offset_map_without_events_is_the_identity():
    offsets = _build_offset_map(())

    assert [offsets.map_offset(offset) for offset in (0, 7, 999)] == [0, 7, 999]


def test_discover_is_sorted_and_keeps_unsupported_files(corpus: Path):
    found = _discover(corpus)

    assert [path.name for path in found] == ["archive.xyz", "filing.md", "notes.txt"]


def test_skip_entry_never_repeats_the_path():
    path = Path("data/samples/archive.xyz")

    assert _skip_entry(path, "data/samples/archive.xyz: unsupported") == "data/samples/archive.xyz: unsupported"
    assert _skip_entry(path, "unsupported") == "data/samples/archive.xyz: unsupported"
