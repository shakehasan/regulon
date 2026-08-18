"""M1 integration test: a corpus on disk becomes a queryable knowledge base.

This is the milestone's acceptance path end to end - ``regulon ingest <path>`` reports chunk
counts - exercised twice: once through :class:`~regulon.ingestion.pipeline.IngestionPipeline`
against a temporary SQLite file, and once through the Typer application. The fixtures are built
here in ``tmp_path`` rather than read from ``data/samples``, so the test states its own inputs and
never touches a network, a model, or a path outside pytest's temporary directory.

The load-bearing assertions are the two that governance depends on: no un-redacted address
survives into the store, and every stored chunk offset still slices its own text out of the
stored document.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from regulon.cli.main import app
from regulon.ingestion.models import IngestReport
from regulon.ingestion.pipeline import IngestionPipeline
from regulon.ingestion.store import SQLiteChunkStore

pytestmark = pytest.mark.integration

# Assembled by concatenation so the literal address never appears in the repository.
EMAIL = "investor" + "." + "relations" + "@" + "example.com"

FILING = f"""---
title: SYNTHETIC Annual Report
ticker: ACME
form: 10-K
fiscal_year: 2024
synthetic: true
---

# Item 1. Business

SYNTHETIC filing. The issuer operates two reportable segments and recorded 1,234,567
in revenue for the fiscal year, against 1,100,000 in the prior year. Segment margins
were stable across both periods, and no customer accounted for more than a tenth of
consolidated revenue.

Investor correspondence is answered at {EMAIL} during business hours.

## Item 1A. Risk Factors

Concentration of suppliers remains the single largest exposure. A disruption at either
of the two contract manufacturers would delay fulfilment by several weeks, and the
issuer holds no long-term commitments that would offset such a delay.

## Item 7. Management Discussion

Operating cash flow covered capital expenditure in each quarter of the year. The board
authorised no repurchases, and the issuer carries no floating-rate debt.
"""

NOTES = """Operating Notes

The segment recorded steady demand across the period. Order intake matched shipments
within a narrow band, and inventory turned at a rate consistent with the prior year.

Nothing in these notes is a filing statement; the corpus is synthetic.
"""


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """Write a small mixed corpus: one markdown filing, one text file, one unparseable file."""
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "SYNTHETIC_ACME_10-K_FY2024.md").write_text(FILING, encoding="utf-8")
    (root / "notes.txt").write_text(NOTES, encoding="utf-8")
    (root / "logo.xyz").write_text("no parser knows this format", encoding="utf-8")
    return root


def ingest(corpus: Path, database: Path) -> IngestReport:
    """Run the pipeline over ``corpus`` into ``database`` with the shipped configuration."""
    with SQLiteChunkStore(database) as store:
        return IngestionPipeline(store).ingest_path(corpus)


def stored_document_texts(database: Path) -> dict[str, str]:
    """Read document text straight from SQLite.

    Schema version 1 exposes chunks but no ``get_document``, and the chunk-offset invariant can
    only be checked against the document text that was actually written.
    """
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute("SELECT document_id, text FROM documents").fetchall()
    finally:
        connection.close()
    return dict(rows)


def test_ingest_reports_documents_chunks_and_redactions(corpus: Path, tmp_path: Path):
    report = ingest(corpus, tmp_path / "regulon.sqlite3")

    assert report.documents_ingested == 2
    assert report.chunks_created > 2
    assert report.redactions_applied == 1
    assert report.chunks_created == sum(summary.chunks for summary in report.documents)
    assert [Path(summary.source_path).name for summary in report.documents] == [
        "SYNTHETIC_ACME_10-K_FY2024.md",
        "notes.txt",
    ]


def test_unsupported_file_is_skipped_with_its_reason(corpus: Path, tmp_path: Path):
    report = ingest(corpus, tmp_path / "regulon.sqlite3")

    assert len(report.skipped) == 1
    assert report.skipped[0].startswith((corpus / "logo.xyz").as_posix() + ": ")
    assert "unsupported extension" in report.skipped[0]


def test_knowledge_base_holds_exactly_what_the_report_claims(corpus: Path, tmp_path: Path):
    database = tmp_path / "regulon.sqlite3"

    report = ingest(corpus, database)

    with SQLiteChunkStore(database) as store:
        assert store.count_documents() == report.documents_ingested
        assert store.count_chunks() == report.chunks_created
        assert store.schema_version() == 1


def test_no_unredacted_address_survives_into_the_store(corpus: Path, tmp_path: Path):
    database = tmp_path / "regulon.sqlite3"

    ingest(corpus, database)

    texts = stored_document_texts(database)
    assert all(EMAIL not in text for text in texts.values())
    assert any("[REDACTED:email]" in text for text in texts.values())
    with SQLiteChunkStore(database) as store:
        chunks = list(store.iter_chunks())
    assert chunks
    assert all(EMAIL not in chunk.text for chunk in chunks)
    assert any("[REDACTED:email]" in chunk.text for chunk in chunks)


def test_stored_chunk_offsets_slice_the_stored_document_text(corpus: Path, tmp_path: Path):
    database = tmp_path / "regulon.sqlite3"

    ingest(corpus, database)

    texts = stored_document_texts(database)
    with SQLiteChunkStore(database) as store:
        chunks = list(store.iter_chunks())
    for chunk in chunks:
        document_text = texts[chunk.document_id]
        assert document_text[chunk.start_char : chunk.end_char] == chunk.text


def test_reingesting_the_same_corpus_changes_nothing(corpus: Path, tmp_path: Path):
    database = tmp_path / "regulon.sqlite3"

    first = ingest(corpus, database)
    with SQLiteChunkStore(database) as store:
        first_chunk_ids = [chunk.chunk_id for chunk in store.iter_chunks()]
    second = ingest(corpus, database)
    with SQLiteChunkStore(database) as store:
        second_chunk_ids = [chunk.chunk_id for chunk in store.iter_chunks()]
        assert store.count_documents() == first.documents_ingested
        assert store.count_chunks() == first.chunks_created

    assert second == first
    assert second_chunk_ids == first_chunk_ids


def test_cli_ingest_reports_the_chunk_count(corpus: Path, tmp_path: Path):
    expected = ingest(corpus, tmp_path / "expected.sqlite3")
    database = tmp_path / "cli.sqlite3"

    result = CliRunner().invoke(app, ["ingest", str(corpus), "--db", str(database)])

    assert result.exit_code == 0, result.output
    assert f"chunks created:     {expected.chunks_created}" in result.stdout
    assert f"documents ingested: {expected.documents_ingested}" in result.stdout
    assert f"redactions applied: {expected.redactions_applied}" in result.stdout
    assert "logo.xyz" in result.stdout
    assert database.is_file()


def test_cli_ingest_emits_the_report_as_json(corpus: Path, tmp_path: Path):
    database = tmp_path / "cli.sqlite3"

    result = CliRunner().invoke(app, ["ingest", str(corpus), "--db", str(database), "--json"])

    assert result.exit_code == 0, result.output
    report = IngestReport.model_validate_json(result.stdout)
    assert report == ingest(corpus, tmp_path / "expected.sqlite3")


def test_cli_ingests_a_single_file_without_a_skipped_section(corpus: Path, tmp_path: Path):
    result = CliRunner().invoke(
        app,
        ["ingest", str(corpus / "notes.txt"), "--db", str(tmp_path / "cli.sqlite3")],
    )

    assert result.exit_code == 0, result.output
    assert "documents ingested: 1" in result.stdout
    assert "skipped" not in result.stdout


def test_cli_defaults_the_database_to_the_configured_data_dir(corpus: Path, tmp_path: Path, monkeypatch):
    data_dir = tmp_path / "configured" / "data"
    monkeypatch.setenv("REGULON_DATA_DIR", str(data_dir))

    result = CliRunner().invoke(app, ["ingest", str(corpus)])

    assert result.exit_code == 0, result.output
    assert (data_dir / "regulon.sqlite3").is_file()


def test_cli_exits_non_zero_when_the_knowledge_base_cannot_be_opened(corpus: Path, tmp_path: Path):
    blocked = tmp_path / "not-a-database"
    blocked.mkdir()

    result = CliRunner().invoke(app, ["ingest", str(corpus), "--db", str(blocked)])

    assert result.exit_code == 1
    assert "error:" in result.stderr
    assert result.stdout == ""


def test_cli_rejects_a_path_that_does_not_exist(tmp_path: Path):
    result = CliRunner().invoke(app, ["ingest", str(tmp_path / "absent")])

    assert result.exit_code != 0


def test_cli_version_prints_the_installed_version():
    from regulon import __version__

    result = CliRunner().invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == __version__
