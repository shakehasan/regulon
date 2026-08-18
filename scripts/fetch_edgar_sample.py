#!/usr/bin/env python3
"""Fetch real SEC EDGAR filings into a local corpus directory, on demand.

**This script requires network access. It is never run in CI and never run by the test suite** -
the EDGAR client it drives (``regulon.ingestion.edgar``) is unit-tested with an injected fake
opener, so the automated gates stay hermetic. Run this yourself when you want real filings to
ingest; everything else in the repo works without it.

**Fetched filings are user-fetched artifacts, not repository content.** They land in
``data/edgar/`` (override with ``--out-dir``), which is not committed: EDGAR filings are public
domain and freely re-fetchable, so the repo carries the command, not the corpus. Keep
``data/edgar/`` out of version control.

Each file is written with a small YAML front-matter header recording the ticker, form, fiscal
year, source URL, and retrieval time, so an ingested chunk can always be traced back to the
filing it came from.

Usage::

    python scripts/fetch_edgar_sample.py --ticker AAPL --form 10-K --limit 1

SEC asks automated callers to identify themselves. Set an identifying User-Agent first::

    export REGULON_INGESTION__EDGAR__USER_AGENT="your-name (https://github.com/your-handle)"

The script prints a warning and keeps going if that variable is still the generic default.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from regulon.core.clock import Clock, SystemClock, isoformat_utc
from regulon.core.config import EdgarSettings, Settings
from regulon.ingestion.edgar import EdgarClient, FilingRef
from regulon.ingestion.errors import EdgarError

_DEFAULT_OUT_DIR = Path("data") / "edgar"
_DEFAULT_FORM = "10-K"
_DEFAULT_LIMIT = 1
_UA_ENV_VAR = "REGULON_INGESTION__EDGAR__USER_AGENT"
_UA_EXAMPLE = "your-name (https://github.com/your-handle)"
# Extensions kept as HTML; anything else is stored as plain text so the ingestion parser can
# pick a source kind from the file name alone.
_HTML_SUFFIXES = frozenset({".htm", ".html"})


class FetchOptions(BaseModel):
    """One invocation's resolved arguments.

    Attributes:
        ticker: Ticker symbol to resolve to a CIK.
        form: Filing form type to fetch, e.g. ``10-K``.
        limit: How many of the most recent matching filings to fetch.
        out_dir: Directory the filings are written to.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: str = Field(min_length=1)
    form: str = Field(min_length=1)
    limit: int = Field(ge=1)
    out_dir: Path


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="fetch_edgar_sample",
        description="Fetch public SEC EDGAR filings into a local directory (requires network access).",
    )
    parser.add_argument("--ticker", required=True, help="Ticker symbol, e.g. AAPL")
    parser.add_argument("--form", default=_DEFAULT_FORM, help=f"Filing form type (default: {_DEFAULT_FORM})")
    parser.add_argument(
        "--limit",
        type=int,
        default=_DEFAULT_LIMIT,
        help=f"How many recent filings to fetch (default: {_DEFAULT_LIMIT})",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=_DEFAULT_OUT_DIR,
        help=f"Output directory (default: {_DEFAULT_OUT_DIR.as_posix()})",
    )
    return parser


def parse_options(argv: list[str] | None = None) -> FetchOptions:
    """Parse command-line arguments into a validated :class:`FetchOptions`."""
    args = build_parser().parse_args(argv)
    return FetchOptions(ticker=args.ticker, form=args.form, limit=args.limit, out_dir=args.out_dir)


def warn_if_default_user_agent(settings: EdgarSettings) -> bool:
    """Warn on stderr when the EDGAR User-Agent is still the repo's generic default.

    Args:
        settings: The EDGAR settings the client will use.

    Returns:
        True if the warning was printed, i.e. the User-Agent is still the shipped default.
    """
    shipped_default = str(EdgarSettings.model_fields["user_agent"].default)
    if settings.user_agent.strip() != shipped_default.strip():
        return False
    print(
        "fetch_edgar_sample: WARNING - the EDGAR User-Agent is still the generic default:\n"
        f"    {settings.user_agent}\n"
        "SEC asks every automated caller to identify itself. Before fetching, set:\n"
        f'    {_UA_ENV_VAR}="{_UA_EXAMPLE}"\n',
        file=sys.stderr,
    )
    return True


def filing_filename(ref: FilingRef, ticker: str) -> str:
    """Build a deterministic, sortable file name for one filing.

    The accession number keeps names unique when a company files the same form twice in a year,
    and the suffix follows the source document so the ingestion parser can tell HTML from text.
    """
    suffix = ".html" if Path(ref.primary_document).suffix.lower() in _HTML_SUFFIXES else ".txt"
    form_slug = ref.form.replace("/", "-").replace(" ", "-").upper()
    accession = ref.accession_number.replace("-", "")
    return f"{ticker.upper()}-{form_slug}-{ref.fiscal_year}-{accession}{suffix}"


def front_matter(ref: FilingRef, ticker: str, retrieved_at: datetime) -> str:
    """Render the YAML front-matter header recording where a filing came from.

    Args:
        ref: The filing that was fetched.
        ticker: Ticker symbol the fetch was requested for.
        retrieved_at: Time the document was downloaded.

    Returns:
        A ``---`` delimited YAML block, terminated by a blank line.
    """
    header = {
        "ticker": ticker.upper(),
        "cik": ref.cik,
        "form": ref.form,
        "fiscal_year": ref.fiscal_year,
        "filing_date": ref.filing_date.isoformat(),
        "accession_number": ref.accession_number,
        "source_url": ref.url,
        "retrieved_at": isoformat_utc(retrieved_at),
        "is_synthetic": False,
        "source": "SEC EDGAR (public domain)",
    }
    body = yaml.safe_dump(header, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return f"---\n{body}---\n\n"


def write_filing(ref: FilingRef, text: str, options: FetchOptions, retrieved_at: datetime) -> Path:
    """Write one filing, front matter first, and return the path written."""
    options.out_dir.mkdir(parents=True, exist_ok=True)
    path = options.out_dir / filing_filename(ref, options.ticker)
    path.write_text(front_matter(ref, options.ticker, retrieved_at) + text, encoding="utf-8")
    return path


def fetch(options: FetchOptions, client: EdgarClient, clock: Clock) -> list[Path]:
    """Fetch the requested filings and write them to disk.

    Args:
        options: Resolved command-line arguments.
        client: EDGAR client to fetch through.
        clock: Supplies the retrieval timestamp recorded in each header.

    Returns:
        The paths written, in fetch order.

    Raises:
        EdgarError: If the lookup, the filing list, or a document fetch fails.
    """
    cik = client.lookup_cik(options.ticker)
    print(f"fetch_edgar_sample: {options.ticker.upper()} -> CIK {cik}")

    refs = client.list_filings(cik, options.form, limit=options.limit)
    if not refs:
        print(f"fetch_edgar_sample: no {options.form} filings found for {options.ticker.upper()}", file=sys.stderr)
        return []

    written: list[Path] = []
    for ref in refs:
        text = client.fetch_filing_text(ref)
        path = write_filing(ref, text, options, clock.now())
        written.append(path)
        print(f"fetch_edgar_sample: wrote {path} ({len(text):,} chars) from {ref.url}")
    return written


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint. Returns the process exit code."""
    options = parse_options(argv)
    settings = Settings().ingestion.edgar
    warn_if_default_user_agent(settings)

    try:
        written = fetch(options, EdgarClient(settings), SystemClock())
    except EdgarError as exc:
        print(f"fetch_edgar_sample: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"fetch_edgar_sample: cannot write to {options.out_dir}: {exc}", file=sys.stderr)
        return 1

    print(f"fetch_edgar_sample: {len(written)} filing(s) written to {options.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
