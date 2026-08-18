"""The ``regulon`` command-line application.

The CLI is deliberately thin: every command resolves configuration, opens the resources its
subsystem needs, calls one object, and renders the model that comes back. No ingestion,
retrieval, or governance logic lives here, so the same behaviour is reachable from Python
without going through a terminal.

Two output modes are supported wherever a command produces a model. The default is a
human-readable summary on stdout; ``--json`` prints the model's JSON instead, so the command can
be piped into a script. Errors always go to stderr and set a non-zero exit code, which keeps
stdout parseable in both modes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from regulon import __version__
from regulon.core.config import load_settings
from regulon.core.errors import RegulonError
from regulon.ingestion.models import IngestReport
from regulon.ingestion.pipeline import IngestionPipeline
from regulon.ingestion.store import SQLiteChunkStore

__all__ = ["app"]

# Where the knowledge base lives is configured (``data_dir``); what it is called is not a
# behaviour tunable, and ``--db`` overrides the whole path anyway.
_DATABASE_FILENAME = "regulon.sqlite3"
_LABEL_WIDTH = 20

app = typer.Typer(
    name="regulon",
    add_completion=False,
    no_args_is_help=True,
    help="Regulon: governed multi-agent RAG you can run locally.",
)


@app.command()
def ingest(
    path: Annotated[
        Path,
        typer.Argument(exists=True, help="File or directory of documents to ingest."),
    ],
    db: Annotated[
        Path | None,
        typer.Option("--db", help="SQLite knowledge base to write. Defaults to <data_dir>/regulon.sqlite3."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print the ingest report as JSON instead of a summary."),
    ] = False,
) -> None:
    """Load, redact, chunk, and store every document under PATH.

    PII is removed before anything is written, so no un-redacted text reaches the knowledge
    base. Files that cannot be parsed are reported as skipped rather than failing the run;
    re-running over the same corpus is idempotent.

    Raises:
        typer.Exit: With code 1 when the knowledge base cannot be opened or written.
    """
    settings = load_settings()
    database = db if db is not None else settings.data_dir / _DATABASE_FILENAME
    try:
        database.parent.mkdir(parents=True, exist_ok=True)
        with SQLiteChunkStore(database) as store:
            report = IngestionPipeline(store, settings=settings).ingest_path(path)
    except (RegulonError, OSError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    _render_ingest_report(report, database, json_output=json_output)


@app.command()
def version() -> None:
    """Print the installed Regulon version."""
    typer.echo(__version__)


def _render_ingest_report(report: IngestReport, database: Path, *, json_output: bool) -> None:
    """Write an ingest report to stdout, as JSON or as a summary.

    Args:
        report: Result of the ingestion run.
        database: Knowledge base the run wrote to, named so a reader knows where it landed.
        json_output: True to print the report model as JSON, False for the summary.
    """
    if json_output:
        typer.echo(report.model_dump_json(indent=2))
        return
    typer.echo(f"{'knowledge base:':<{_LABEL_WIDTH}}{database.as_posix()}")
    typer.echo(f"{'documents ingested:':<{_LABEL_WIDTH}}{report.documents_ingested}")
    typer.echo(f"{'chunks created:':<{_LABEL_WIDTH}}{report.chunks_created}")
    typer.echo(f"{'redactions applied:':<{_LABEL_WIDTH}}{report.redactions_applied}")
    for summary in report.documents:
        typer.echo(f"  {summary.source_path}: {summary.chunks} chunks, {summary.redactions} redactions")
    if report.skipped:
        typer.echo(f"skipped {len(report.skipped)} file(s):")
        for entry in report.skipped:
            typer.echo(f"  {entry}")


if __name__ == "__main__":  # pragma: no cover - covered by a subprocess test, not by the importing run
    app()
