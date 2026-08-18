"""The SQLite representation behind :mod:`regulon.ingestion.store`: statements and row mapping.

Everything that knows about column names lives here, separate from the store's connection and
transaction behavior, so a migration in a later milestone is a diff against one file. Nothing in
this module opens a connection or executes SQL; :class:`~regulon.ingestion.store.SQLiteChunkStore`
runs these statements with bound parameters and hands rows back for mapping.

Two constraints in the schema carry meaning:

* ``chunks.document_id`` is a foreign key, so a chunk cannot be stored for a document that was
  never ingested, and ``source_path`` need not be duplicated onto chunk rows - it is read back
  through the join.
* ``UNIQUE (document_id, chunk_index)`` keeps one chunk per slot, which makes re-chunking a
  stored document a replacement rather than a silent duplication.
"""

from __future__ import annotations

import sqlite3
from typing import TypeAlias

from regulon.ingestion.models import Chunk, ChunkMetadata, NormalizedDocument

SqlValue: TypeAlias = str | int | float | bytes | None
"""Every Python type this schema binds into a statement or reads out of a row."""

SCHEMA_VERSION = 1
"""Version of the on-disk schema this package reads and writes."""

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        id      INTEGER PRIMARY KEY CHECK (id = 1),
        version INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS documents (
        document_id  TEXT    PRIMARY KEY,
        source_path  TEXT    NOT NULL,
        kind         TEXT    NOT NULL,
        title        TEXT,
        ticker       TEXT,
        form         TEXT,
        fiscal_year  INTEGER,
        is_synthetic INTEGER NOT NULL,
        text         TEXT    NOT NULL,
        created_at   TEXT    NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chunks (
        chunk_id        TEXT    PRIMARY KEY,
        document_id     TEXT    NOT NULL REFERENCES documents (document_id) ON DELETE CASCADE,
        chunk_index     INTEGER NOT NULL,
        text            TEXT    NOT NULL,
        start_char      INTEGER NOT NULL,
        end_char        INTEGER NOT NULL,
        section_heading TEXT,
        ticker          TEXT,
        form            TEXT,
        fiscal_year     INTEGER,
        is_synthetic    INTEGER NOT NULL,
        UNIQUE (document_id, chunk_index)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON chunks (document_id)",
)

ENABLE_FOREIGN_KEYS = "PRAGMA foreign_keys = ON"

INIT_SCHEMA_VERSION = "INSERT INTO schema_version (id, version) VALUES (1, ?) ON CONFLICT (id) DO NOTHING"
SELECT_SCHEMA_VERSION = "SELECT version FROM schema_version WHERE id = 1"

# ``document_id`` hashes the text and the source path, so a conflict means those two columns
# already match; only the filing metadata can have changed. ``created_at`` is deliberately absent
# from the update, so it records first ingestion rather than the latest replay.
UPSERT_DOCUMENT = """
INSERT INTO documents (
    document_id, source_path, kind, title, ticker, form, fiscal_year, is_synthetic, text, created_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (document_id) DO UPDATE SET
    title = excluded.title,
    ticker = excluded.ticker,
    form = excluded.form,
    fiscal_year = excluded.fiscal_year,
    is_synthetic = excluded.is_synthetic
"""

# Re-chunking a stored document with different settings gives the same ``(document_id,
# chunk_index)`` slot a new chunk id; clearing the previous occupant keeps the unique constraint
# from turning a legitimate re-ingest into a failure. An explicit DELETE is used rather than
# INSERT OR REPLACE so that rows referencing surviving chunks are never cascade-deleted.
DELETE_REPLACED_CHUNK = "DELETE FROM chunks WHERE document_id = ? AND chunk_index = ? AND chunk_id <> ?"

UPSERT_CHUNK = """
INSERT INTO chunks (
    chunk_id, document_id, chunk_index, text, start_char, end_char,
    section_heading, ticker, form, fiscal_year, is_synthetic
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (chunk_id) DO UPDATE SET
    document_id = excluded.document_id,
    chunk_index = excluded.chunk_index,
    text = excluded.text,
    start_char = excluded.start_char,
    end_char = excluded.end_char,
    section_heading = excluded.section_heading,
    ticker = excluded.ticker,
    form = excluded.form,
    fiscal_year = excluded.fiscal_year,
    is_synthetic = excluded.is_synthetic
"""

_SELECT_CHUNKS = """
SELECT c.chunk_id, c.document_id, c.chunk_index, c.text, c.start_char, c.end_char,
       c.section_heading, c.ticker, c.form, c.fiscal_year, c.is_synthetic, d.source_path
FROM chunks AS c
JOIN documents AS d ON d.document_id = c.document_id
"""
_ORDER_BY_DOCUMENT_THEN_INDEX = "ORDER BY c.document_id, c.chunk_index"

SELECT_CHUNK_BY_ID = f"{_SELECT_CHUNKS}WHERE c.chunk_id = ?"
SELECT_ALL_CHUNKS = f"{_SELECT_CHUNKS}{_ORDER_BY_DOCUMENT_THEN_INDEX}"
SELECT_CHUNKS_BY_DOCUMENT = f"{_SELECT_CHUNKS}WHERE c.document_id = ?\n{_ORDER_BY_DOCUMENT_THEN_INDEX}"

COUNT_DOCUMENTS = "SELECT COUNT(*) FROM documents"
COUNT_CHUNKS = "SELECT COUNT(*) FROM chunks"


def document_row(document: NormalizedDocument, created_at: str) -> tuple[SqlValue, ...]:
    """Flatten a document into its ``documents`` row, in the column order of :data:`UPSERT_DOCUMENT`."""
    metadata = document.metadata
    return (
        document.document_id,
        metadata.source_path,
        document.kind.value,
        metadata.title,
        metadata.ticker,
        metadata.form,
        metadata.fiscal_year,
        int(metadata.is_synthetic),
        document.text,
        created_at,
    )


def chunk_row(chunk: Chunk) -> tuple[SqlValue, ...]:
    """Flatten a chunk into its ``chunks`` row, in the column order of :data:`UPSERT_CHUNK`."""
    metadata = chunk.metadata
    return (
        chunk.chunk_id,
        chunk.document_id,
        metadata.chunk_index,
        chunk.text,
        chunk.start_char,
        chunk.end_char,
        metadata.section_heading,
        metadata.ticker,
        metadata.form,
        metadata.fiscal_year,
        int(metadata.is_synthetic),
    )


def row_to_chunk(row: sqlite3.Row) -> Chunk:
    """Rebuild a chunk from a row of the chunks/documents join.

    ``source_path`` comes from the joined document because chunk rows do not store it; the
    foreign key guarantees that joined row exists. Booleans arrive as SQLite integers and are
    converted back here.
    """
    return Chunk(
        chunk_id=row["chunk_id"],
        document_id=row["document_id"],
        text=row["text"],
        start_char=row["start_char"],
        end_char=row["end_char"],
        metadata=ChunkMetadata(
            document_id=row["document_id"],
            source_path=row["source_path"],
            chunk_index=row["chunk_index"],
            section_heading=row["section_heading"],
            ticker=row["ticker"],
            form=row["form"],
            fiscal_year=row["fiscal_year"],
            is_synthetic=bool(row["is_synthetic"]),
        ),
    )
