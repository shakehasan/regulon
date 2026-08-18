"""SQLite-backed knowledge base for ingested documents and chunks.

Ingestion ends by writing to a store and retrieval (M2) begins by reading from one, so
:class:`ChunkStore` is the seam between those halves: the pipeline depends on the protocol and
never on SQLite, which lets a pgvector-backed implementation drop in later without a pipeline
change. :class:`SQLiteChunkStore` is the zero-cost default - one file on disk, no server and no
account, with ``Path(":memory:")`` for tests.

Three properties are worth knowing before building on it:

* **Provenance is normalized.** A chunk row stores no ``source_path``; it is read back from the
  owning document row through the foreign key, so a document can never disagree with its own
  chunks about where it came from.
* **Writes are idempotent.** Documents and chunks upsert on their content-hash primary keys, so
  re-running ingestion over the same corpus converges instead of raising or duplicating rows.
* **Version 1 is deliberately narrow.** It stores document text and filing metadata but not the
  :class:`~regulon.ingestion.models.Section` map or ``DocumentMetadata.extra``; chunks carry the
  denormalized metadata that retrieval filters on. The ``schema_version`` table exists so a later
  milestone migrates rather than guesses, and a database written by a newer version is rejected.

Every statement is parameterized, and every :class:`sqlite3.Error` surfaces as a
:class:`~regulon.ingestion.errors.StoreError` so callers catch one subsystem exception type.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Protocol, Self, runtime_checkable

from regulon.core.clock import Clock, SystemClock, isoformat_utc
from regulon.ingestion import store_sql
from regulon.ingestion.errors import StoreError
from regulon.ingestion.models import Chunk, NormalizedDocument
from regulon.ingestion.store_sql import SCHEMA_VERSION, SqlValue

__all__ = ["SCHEMA_VERSION", "ChunkStore", "SQLiteChunkStore"]


@runtime_checkable
class ChunkStore(Protocol):
    """The knowledge-base interface ingestion writes to and retrieval reads from.

    Implementations must preserve two guarantees the pipeline relies on: writes are idempotent
    by id, so re-ingesting a corpus converges; and :meth:`iter_chunks` yields an order fixed by
    the data rather than by insertion history.
    """

    def add_document(self, document: NormalizedDocument) -> None:
        """Persist a document, replacing any stored copy carrying the same id.

        Raises:
            StoreError: If the write fails.
        """
        ...

    def add_chunks(self, chunks: Sequence[Chunk]) -> int:
        """Persist chunks, replacing any stored copies carrying the same ids.

        Args:
            chunks: Chunks to store; each chunk's document must already be stored.

        Returns:
            The number of chunks written.

        Raises:
            StoreError: If the write fails, e.g. a chunk references an unknown document.
        """
        ...

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        """Return the chunk with this id, or None when no chunk carries it.

        Raises:
            StoreError: If the read fails.
        """
        ...

    def iter_chunks(self, *, document_id: str | None = None) -> Iterator[Chunk]:
        """Yield stored chunks ordered by ``(document_id, chunk_index)``.

        Args:
            document_id: Restrict the iteration to one document, or None for every chunk.

        Yields:
            Chunks in a deterministic order.

        Raises:
            StoreError: If the read fails.
        """
        ...

    def count_documents(self) -> int:
        """Return the number of stored documents.

        Raises:
            StoreError: If the read fails.
        """
        ...

    def count_chunks(self) -> int:
        """Return the number of stored chunks.

        Raises:
            StoreError: If the read fails.
        """
        ...

    def close(self) -> None:
        """Release the store's resources. Calling it more than once is a no-op."""
        ...


class SQLiteChunkStore:
    """A :class:`ChunkStore` held in a single SQLite database.

    The store owns one connection for its lifetime, so ``Path(":memory:")`` behaves like a real
    database for the duration of a test. Use it as a context manager, or call :meth:`close`.
    """

    def __init__(self, db_path: Path, *, clock: Clock | None = None) -> None:
        """Open the database at ``db_path``, creating the schema when it is absent.

        Args:
            db_path: File to open, or ``Path(":memory:")`` for an ephemeral database. The parent
                directory must already exist.
            clock: Clock stamping ``created_at``; defaults to system time.

        Raises:
            StoreError: If the database cannot be opened, the schema cannot be created, or the
                database was written by a schema version newer than :data:`SCHEMA_VERSION`.
        """
        self._db_path = db_path
        self._clock = clock if clock is not None else SystemClock()
        self._connection = self._connect()
        self._ensure_schema()

    @property
    def db_path(self) -> Path:
        """Path the store was opened at."""
        return self._db_path

    def add_document(self, document: NormalizedDocument) -> None:
        """Upsert one document row, leaving ``created_at`` at its first-ingestion value.

        Raises:
            StoreError: If the write fails.
        """
        created_at = isoformat_utc(self._clock.now())
        with self._transaction() as connection:
            connection.execute(store_sql.UPSERT_DOCUMENT, store_sql.document_row(document, created_at))

    def add_chunks(self, chunks: Sequence[Chunk]) -> int:
        """Upsert chunk rows in one transaction, so a failed batch writes nothing.

        Args:
            chunks: Chunks to store; each chunk's document must already be stored.

        Returns:
            The number of chunks written; zero for an empty sequence.

        Raises:
            StoreError: If a chunk disagrees with its own metadata about which document it
                belongs to, or if the write fails, e.g. an unknown document id.
        """
        if not chunks:
            return 0
        for chunk in chunks:
            if chunk.document_id != chunk.metadata.document_id:
                raise StoreError(
                    f"chunk {chunk.chunk_id} claims document {chunk.document_id} "
                    f"but its metadata claims {chunk.metadata.document_id}"
                )
        replaced = [(chunk.document_id, chunk.metadata.chunk_index, chunk.chunk_id) for chunk in chunks]
        with self._transaction() as connection:
            connection.executemany(store_sql.DELETE_REPLACED_CHUNK, replaced)
            connection.executemany(store_sql.UPSERT_CHUNK, [store_sql.chunk_row(chunk) for chunk in chunks])
        return len(chunks)

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        """Return the chunk with this id, or None when no chunk carries it.

        Raises:
            StoreError: If the read fails.
        """
        row = self._fetch_one(store_sql.SELECT_CHUNK_BY_ID, (chunk_id,))
        return None if row is None else store_sql.row_to_chunk(row)

    def iter_chunks(self, *, document_id: str | None = None) -> Iterator[Chunk]:
        """Stream chunks ordered by ``(document_id, chunk_index)`` without materializing them.

        Args:
            document_id: Restrict the iteration to one document, or None for every chunk.

        Yields:
            Chunks in an order fixed by the data, not by insertion order.

        Raises:
            StoreError: If the read fails.
        """
        statement = store_sql.SELECT_ALL_CHUNKS if document_id is None else store_sql.SELECT_CHUNKS_BY_DOCUMENT
        parameters: tuple[SqlValue, ...] = () if document_id is None else (document_id,)
        try:
            for row in self._connection.execute(statement, parameters):
                yield store_sql.row_to_chunk(row)
        except sqlite3.Error as exc:
            raise StoreError(f"cannot read chunks from the knowledge base at {self._db_path}") from exc

    def count_documents(self) -> int:
        """Return the number of stored documents.

        Raises:
            StoreError: If the read fails.
        """
        return self._count(store_sql.COUNT_DOCUMENTS)

    def count_chunks(self) -> int:
        """Return the number of stored chunks.

        Raises:
            StoreError: If the read fails.
        """
        return self._count(store_sql.COUNT_CHUNKS)

    def schema_version(self) -> int:
        """Return the schema version recorded in the database.

        Raises:
            StoreError: If the read fails or no version is recorded.
        """
        row = self._fetch_one(store_sql.SELECT_SCHEMA_VERSION, ())
        if row is None:  # pragma: no cover - the row is written when the schema is created
            raise StoreError(f"knowledge base at {self._db_path} records no schema version")
        return int(row["version"])

    def close(self) -> None:
        """Close the underlying connection; calling it again is a no-op.

        Raises:
            StoreError: If the connection cannot be closed.
        """
        try:
            self._connection.close()
        except sqlite3.Error as exc:  # pragma: no cover - closing twice is allowed by sqlite3
            raise StoreError(f"cannot close the knowledge base at {self._db_path}") from exc

    def __enter__(self) -> Self:
        """Return the open store."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the store, whether or not the block raised."""
        self.close()

    def _connect(self) -> sqlite3.Connection:
        """Open the connection with foreign keys enforced and rows addressable by name.

        Raises:
            StoreError: If the database cannot be opened.
        """
        try:
            connection = sqlite3.connect(str(self._db_path))
            connection.row_factory = sqlite3.Row
            connection.execute(store_sql.ENABLE_FOREIGN_KEYS)
        except sqlite3.Error as exc:
            raise StoreError(f"cannot open the knowledge base at {self._db_path}") from exc
        return connection

    def _ensure_schema(self) -> None:
        """Create the schema when absent and refuse a database from a newer version.

        Raises:
            StoreError: If the schema cannot be created or the stored version is unsupported.
        """
        with self._transaction() as connection:
            for statement in store_sql.SCHEMA_STATEMENTS:
                connection.execute(statement)
            connection.execute(store_sql.INIT_SCHEMA_VERSION, (SCHEMA_VERSION,))
        stored = self.schema_version()
        if stored > SCHEMA_VERSION:
            self.close()
            raise StoreError(
                f"knowledge base at {self._db_path} uses schema version {stored}, "
                f"newer than the supported version {SCHEMA_VERSION}"
            )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a block in one transaction, committing on success and rolling back on failure.

        Raises:
            StoreError: If any statement in the block fails.
        """
        try:
            with self._connection:
                yield self._connection
        except sqlite3.Error as exc:
            raise StoreError(f"cannot write to the knowledge base at {self._db_path}") from exc

    def _fetch_one(self, statement: str, parameters: tuple[SqlValue, ...]) -> sqlite3.Row | None:
        """Return the first row of a query, or None when it selected nothing.

        Raises:
            StoreError: If the read fails.
        """
        try:
            row: sqlite3.Row | None = self._connection.execute(statement, parameters).fetchone()
        except sqlite3.Error as exc:
            raise StoreError(f"cannot read from the knowledge base at {self._db_path}") from exc
        return row

    def _count(self, statement: str) -> int:
        """Return the single integer a counting query produces.

        Raises:
            StoreError: If the read fails.
        """
        row = self._fetch_one(statement, ())
        if row is None:  # pragma: no cover - COUNT(*) always returns a row
            return 0
        return int(row[0])


if TYPE_CHECKING:
    # Type-checked proof that the SQLite implementation satisfies the swappable interface, so a
    # signature drift fails `make type` rather than some later import.
    _protocol_check: type[ChunkStore] = SQLiteChunkStore
