"""Tests for the SQLite knowledge-base store.

Every test runs against a temporary file or an in-memory database - nothing here touches a
network, a server, or a path outside pytest's ``tmp_path``. Sample text is synthetic and carries
no personal data.
"""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from regulon.core.clock import FixedClock
from regulon.ingestion import (
    Chunk,
    ChunkMetadata,
    DocumentMetadata,
    NormalizedDocument,
    Section,
    SourceKind,
    StoreError,
    make_chunk_id,
    make_document_id,
)
from regulon.ingestion.store import SCHEMA_VERSION, ChunkStore, SQLiteChunkStore

DOC_TEXT = "Item 1. Business\nThe issuer operates two segments.\nItem 1A. Risk Factors\nMarkets move."
SOURCE_PATH = "data/synthetic/SYNTHETIC-acme-10k.md"
OTHER_SOURCE_PATH = "data/synthetic/SYNTHETIC-globex-10k.md"


def build_document(source_path: str = SOURCE_PATH, text: str = DOC_TEXT) -> NormalizedDocument:
    metadata = DocumentMetadata(
        source_path=source_path,
        title="SYNTHETIC Annual Report",
        ticker="ACME",
        form="10-K",
        fiscal_year=2024,
        is_synthetic=True,
    )
    return NormalizedDocument(
        document_id=make_document_id(text, source_path),
        kind=SourceKind.MARKDOWN,
        text=text,
        sections=[Section(order=0, heading="Item 1. Business", text=text, start_char=0, end_char=len(text))],
        metadata=metadata,
    )


def build_chunk(document: NormalizedDocument, index: int, *, text: str | None = None) -> Chunk:
    span = 20
    start = index * span
    body = text if text is not None else document.text[start : start + span]
    return Chunk(
        chunk_id=make_chunk_id(document.document_id, index, body),
        document_id=document.document_id,
        text=body,
        start_char=start,
        end_char=start + len(body),
        metadata=ChunkMetadata(
            document_id=document.document_id,
            source_path=document.metadata.source_path,
            chunk_index=index,
            section_heading="Item 1. Business",
            ticker=document.metadata.ticker,
            form=document.metadata.form,
            fiscal_year=document.metadata.fiscal_year,
            is_synthetic=document.metadata.is_synthetic,
        ),
    )


def build_chunks(document: NormalizedDocument, count: int) -> list[Chunk]:
    return [build_chunk(document, index) for index in range(count)]


@pytest.fixture
def store() -> Iterator[SQLiteChunkStore]:
    with SQLiteChunkStore(Path(":memory:")) as open_store:
        yield open_store


def read_column(db_path: Path, statement: str) -> object:
    connection = sqlite3.connect(str(db_path))
    try:
        return connection.execute(statement).fetchone()[0]
    finally:
        connection.close()


def test_sqlite_store_satisfies_the_chunk_store_protocol(store):
    assert isinstance(store, ChunkStore)


def test_new_database_records_schema_version_one(store):
    assert SCHEMA_VERSION == 1
    assert store.schema_version() == 1


def test_empty_store_counts_zero(store):
    assert store.count_documents() == 0
    assert store.count_chunks() == 0


def test_document_and_chunks_round_trip(store):
    document = build_document()
    chunks = build_chunks(document, 3)

    store.add_document(document)
    written = store.add_chunks(chunks)

    assert written == 3
    assert store.count_documents() == 1
    assert store.count_chunks() == 3
    assert [chunk.chunk_id for chunk in store.iter_chunks()] == [chunk.chunk_id for chunk in chunks]


def test_chunk_survives_round_trip_with_every_metadata_field(store):
    document = build_document()
    chunk = build_chunk(document, 0)
    store.add_document(document)
    store.add_chunks([chunk])

    loaded = store.get_chunk(chunk.chunk_id)

    assert loaded == chunk
    assert loaded is not None
    assert loaded.metadata.source_path == SOURCE_PATH
    assert loaded.metadata.section_heading == "Item 1. Business"
    assert loaded.metadata.ticker == "ACME"
    assert loaded.metadata.form == "10-K"
    assert loaded.metadata.fiscal_year == 2024
    assert loaded.metadata.is_synthetic is True


def test_optional_metadata_round_trips_as_none(store):
    text = "A short synthetic note."
    metadata = DocumentMetadata(source_path="data/synthetic/SYNTHETIC-note.txt")
    document = NormalizedDocument(
        document_id=make_document_id(text, metadata.source_path),
        kind=SourceKind.TEXT,
        text=text,
        sections=[],
        metadata=metadata,
    )
    chunk = Chunk(
        chunk_id=make_chunk_id(document.document_id, 0, text),
        document_id=document.document_id,
        text=text,
        start_char=0,
        end_char=len(text),
        metadata=ChunkMetadata(document_id=document.document_id, source_path=metadata.source_path, chunk_index=0),
    )
    store.add_document(document)
    store.add_chunks([chunk])

    loaded = store.get_chunk(chunk.chunk_id)

    assert loaded == chunk
    assert loaded is not None
    assert loaded.metadata.section_heading is None
    assert loaded.metadata.ticker is None
    assert loaded.metadata.fiscal_year is None
    assert loaded.metadata.is_synthetic is False


def test_get_chunk_returns_none_for_an_unknown_id(store):
    document = build_document()
    store.add_document(document)
    store.add_chunks(build_chunks(document, 2))

    assert store.get_chunk("chk_0000000000000000") is None


def test_add_chunks_with_an_empty_sequence_writes_nothing(store):
    store.add_document(build_document())

    assert store.add_chunks([]) == 0
    assert store.count_chunks() == 0


def test_iter_chunks_filters_by_document(store):
    first = build_document()
    second = build_document(source_path=OTHER_SOURCE_PATH)
    for document in (first, second):
        store.add_document(document)
        store.add_chunks(build_chunks(document, 2))

    selected = list(store.iter_chunks(document_id=second.document_id))

    assert store.count_chunks() == 4
    assert [chunk.metadata.chunk_index for chunk in selected] == [0, 1]
    assert {chunk.document_id for chunk in selected} == {second.document_id}


def test_iter_chunks_filter_on_an_unknown_document_yields_nothing(store):
    document = build_document()
    store.add_document(document)
    store.add_chunks(build_chunks(document, 2))

    assert list(store.iter_chunks(document_id="doc_0000000000000000")) == []


def test_iteration_order_is_deterministic_regardless_of_insertion_order(store):
    first = build_document()
    second = build_document(source_path=OTHER_SOURCE_PATH)
    chunks = build_chunks(first, 3) + build_chunks(second, 3)
    for document in (first, second):
        store.add_document(document)
    store.add_chunks(list(reversed(chunks)))

    ordered = list(store.iter_chunks())

    expected = sorted(chunks, key=lambda chunk: (chunk.document_id, chunk.metadata.chunk_index))
    assert [chunk.chunk_id for chunk in ordered] == [chunk.chunk_id for chunk in expected]


def test_re_ingesting_the_same_document_is_idempotent(store):
    document = build_document()
    chunks = build_chunks(document, 3)

    for _ in range(3):
        store.add_document(document)
        assert store.add_chunks(chunks) == 3

    assert store.count_documents() == 1
    assert store.count_chunks() == 3
    assert [chunk.chunk_id for chunk in store.iter_chunks()] == [chunk.chunk_id for chunk in chunks]


def test_re_ingest_updates_changed_document_metadata(tmp_path: Path):
    db_path = tmp_path / "kb.sqlite3"
    document = build_document()
    revised = document.model_copy(update={"metadata": document.metadata.model_copy(update={"ticker": "ACMX"})})

    with SQLiteChunkStore(db_path) as open_store:
        open_store.add_document(document)
        open_store.add_document(revised)

        assert open_store.count_documents() == 1

    assert read_column(db_path, "SELECT ticker FROM documents") == "ACMX"


def test_re_chunking_replaces_the_chunk_holding_an_index(store):
    document = build_document()
    store.add_document(document)
    original = build_chunk(document, 0)
    store.add_chunks([original])

    rechunked = build_chunk(document, 0, text="A wider slice of the same synthetic filing text.")
    store.add_chunks([rechunked])

    assert original.chunk_id != rechunked.chunk_id
    assert store.count_chunks() == 1
    assert store.get_chunk(original.chunk_id) is None
    assert store.get_chunk(rechunked.chunk_id) == rechunked


def test_chunk_for_an_unknown_document_is_rejected_by_the_foreign_key(store):
    document = build_document()

    with pytest.raises(StoreError) as excinfo:
        store.add_chunks([build_chunk(document, 0)])

    assert isinstance(excinfo.value.__cause__, sqlite3.IntegrityError)
    assert store.count_chunks() == 0


def test_chunk_disagreeing_with_its_metadata_is_rejected(store):
    document = build_document()
    store.add_document(document)
    chunk = build_chunk(document, 0)
    mismatched = chunk.model_copy(
        update={"metadata": chunk.metadata.model_copy(update={"document_id": "doc_0000000000000000"})}
    )

    with pytest.raises(StoreError, match="metadata claims"):
        store.add_chunks([mismatched])

    assert store.count_chunks() == 0


def test_data_persists_across_store_instances(tmp_path: Path):
    db_path = tmp_path / "kb.sqlite3"
    document = build_document()
    with SQLiteChunkStore(db_path) as first:
        first.add_document(document)
        first.add_chunks(build_chunks(document, 2))

    with SQLiteChunkStore(db_path) as second:
        assert second.count_documents() == 1
        assert second.count_chunks() == 2
        assert second.schema_version() == SCHEMA_VERSION


def test_context_manager_closes_the_store(tmp_path: Path):
    db_path = tmp_path / "kb.sqlite3"
    with SQLiteChunkStore(db_path) as open_store:
        open_store.add_document(build_document())
        closed = open_store

    with pytest.raises(StoreError):
        closed.count_documents()


def test_close_is_idempotent(tmp_path: Path):
    open_store = SQLiteChunkStore(tmp_path / "kb.sqlite3")
    open_store.close()
    open_store.close()

    with pytest.raises(StoreError):
        open_store.add_document(build_document())


def test_reads_after_close_raise_store_error(tmp_path: Path):
    document = build_document()
    open_store = SQLiteChunkStore(tmp_path / "kb.sqlite3")
    open_store.add_document(document)
    open_store.close()

    with pytest.raises(StoreError):
        list(open_store.iter_chunks())
    with pytest.raises(StoreError):
        open_store.get_chunk("chk_0000000000000000")


def test_unopenable_database_path_raises_store_error(tmp_path: Path):
    unreachable = tmp_path / "no-such-directory" / "kb.sqlite3"

    with pytest.raises(StoreError, match="cannot open"):
        SQLiteChunkStore(unreachable)


def test_database_from_a_newer_schema_version_is_rejected(tmp_path: Path):
    db_path = tmp_path / "kb.sqlite3"
    SQLiteChunkStore(db_path).close()
    connection = sqlite3.connect(str(db_path))
    with connection:
        connection.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION + 1,))
    connection.close()

    with pytest.raises(StoreError, match="newer than the supported version"):
        SQLiteChunkStore(db_path)


def test_store_exposes_its_path(tmp_path: Path):
    db_path = tmp_path / "kb.sqlite3"
    with SQLiteChunkStore(db_path) as open_store:
        assert open_store.db_path == db_path


def test_document_created_at_records_first_ingestion(tmp_path: Path):
    db_path = tmp_path / "kb.sqlite3"
    document = build_document()
    first_ingest = FixedClock(datetime(2024, 3, 1, 12, 0, tzinfo=UTC))
    later_replay = FixedClock(datetime(2025, 6, 2, 9, 30, tzinfo=UTC))

    with SQLiteChunkStore(db_path, clock=first_ingest) as open_store:
        open_store.add_document(document)
    with SQLiteChunkStore(db_path, clock=later_replay) as reopened:
        reopened.add_document(document)

    assert read_column(db_path, "SELECT created_at FROM documents") == "2024-03-01T12:00:00Z"
