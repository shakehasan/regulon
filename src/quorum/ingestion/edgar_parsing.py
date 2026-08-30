"""Parsing and URL helpers for the EDGAR client.

Split out of :mod:`quorum.ingestion.edgar` so the client module stays inside the repository's
400-line ceiling (AGENTS.md). Everything here is a pure function of its arguments: no sockets, no
clock, no client state. That is what makes the awkward part of talking to EDGAR - a JSON feed of
parallel arrays, two hosts, and dates that may or may not be present - testable in isolation.

Every failure names the URL it came from and surfaces as
:class:`~quorum.ingestion.errors.EdgarError`, never as a raw ``JSONDecodeError`` or ``KeyError``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field

from quorum.ingestion.errors import EdgarError

# Endpoint layout of the public EDGAR API. Not tunables: they describe how SEC publishes its data.
_ARCHIVES_PATH = "/Archives/edgar/data/{cik}/{accession}/{document}"
_CIK_DIGITS = 10
_WWW_LABEL = "www."
_DATA_LABEL = "data."
# Text encodings EDGAR documents actually use, tried in order; the fallback never raises.
_PRIMARY_ENCODING = "utf-8"
_FALLBACK_ENCODING = "cp1252"


class FilingRef(BaseModel):
    """A pointer to one filing document on EDGAR.

    Attributes:
        cik: Zero-padded ten-digit Central Index Key of the filer.
        accession_number: Dashed accession number, e.g. ``0000000000-24-000001``.
        form: Form type exactly as EDGAR reports it, e.g. ``10-K``.
        filing_date: Date EDGAR accepted the filing.
        primary_document: File name of the filing's primary document.
        url: Absolute URL of that document under ``/Archives``.
        report_date: Period the filing reports on, when EDGAR states one. Its year is the fiscal
            year the filing covers, which for an annual report is usually not the year it was
            filed in - hence :attr:`fiscal_year` rather than a bare ``filing_date.year``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    cik: str = Field(min_length=1)
    accession_number: str = Field(min_length=1)
    form: str = Field(min_length=1)
    filing_date: date
    primary_document: str = Field(min_length=1)
    url: str = Field(min_length=1)
    report_date: date | None = None

    @property
    def fiscal_year(self) -> int:
        """Return the fiscal year the filing covers: the report period's year if EDGAR gave one."""
        return (self.report_date or self.filing_date).year


def _submissions_base_url(base_url: str) -> str:
    """Return the origin that serves the submissions JSON API for ``base_url``.

    EDGAR splits its endpoints across two hosts: documents and the company index sit on the main
    site, while the submissions API is served from its ``data.`` sibling. Deriving the second host
    from the first keeps a single ``base_url`` setting authoritative. Hosts that do not start with
    ``www.`` (a local fake, for instance) are used unchanged.
    """
    parts = urlsplit(base_url)
    host = parts.netloc
    if host.startswith(_WWW_LABEL):
        host = _DATA_LABEL + host.removeprefix(_WWW_LABEL)
    return urlunsplit((parts.scheme, host, "", "", ""))


def _normalize_cik(value: object) -> str:
    """Return ``value`` as a zero-padded ten-digit CIK.

    Accepts what EDGAR and its users actually write: an int, ``"320193"``, ``"0000320193"``, or
    ``"CIK0000320193"``.

    Raises:
        ValueError: If the value is not a run of at most ten digits.
    """
    text = str(value).strip().upper().removeprefix("CIK").strip()
    if not text.isdigit() or len(text) > _CIK_DIGITS:
        raise ValueError(f"{value!r} is not a CIK (expected up to {_CIK_DIGITS} digits)")
    return text.zfill(_CIK_DIGITS)


def _decode(payload: bytes) -> str:
    """Decode filing bytes, falling back to the legacy encoding older EDGAR documents use."""
    try:
        return payload.decode(_PRIMARY_ENCODING)
    except UnicodeDecodeError:
        return payload.decode(_FALLBACK_ENCODING, errors="replace")


def _json_object(raw: bytes, url: str) -> Mapping[str, object]:
    """Parse a response body as a JSON object.

    Raises:
        EdgarError: If the body is not decodable, not valid JSON, or not a JSON object.
    """
    try:
        decoded = json.loads(raw.decode(_PRIMARY_ENCODING))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EdgarError(f"EDGAR returned unreadable JSON from {url}: {exc}") from exc
    if not isinstance(decoded, dict):
        raise EdgarError(f"EDGAR returned {type(decoded).__name__} from {url}, expected a JSON object")
    return {str(key): value for key, value in decoded.items()}


def _mapping(value: object, what: str, url: str) -> Mapping[str, object]:
    """Return ``value`` as a mapping, or raise :class:`EdgarError` naming ``what`` and ``url``."""
    if not isinstance(value, Mapping):
        raise EdgarError(f"EDGAR response from {url} has no {what} object")
    return {str(key): item for key, item in value.items()}


def _column(recent: Mapping[str, object], name: str, url: str) -> Sequence[object]:
    """Return one parallel column of the submissions feed.

    Raises:
        EdgarError: If the column is missing or is not a list.
    """
    value = recent.get(name)
    if not isinstance(value, list):
        raise EdgarError(f"EDGAR submissions response from {url} is missing the {name!r} column")
    return value


def _text(value: object, what: str, url: str) -> str:
    """Return ``value`` as a non-empty string, or raise :class:`EdgarError`."""
    if not isinstance(value, str) or not value.strip():
        raise EdgarError(f"EDGAR response from {url} has an empty or non-string {what}")
    return value.strip()


def _iso_date(value: object, what: str, url: str) -> date:
    """Parse an ISO ``YYYY-MM-DD`` date from a response field.

    Raises:
        EdgarError: If the field is absent, not a string, or not an ISO date.
    """
    text = _text(value, what, url)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise EdgarError(f"EDGAR response from {url} has an unparsable {what} {text!r}: {exc}") from exc


def _optional_iso_date(value: object) -> date | None:
    """Parse an optional ISO date, treating anything unusable as absent.

    ``reportDate`` is blank for many filing types, and a missing report period is not a failure -
    it only means :attr:`FilingRef.fiscal_year` falls back to the filing year.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _filing_url(base_url: str, cik: str, accession_number: str, document: str) -> str:
    """Build the ``/Archives`` URL of one filing document (the archive path uses an unpadded CIK)."""
    path = _ARCHIVES_PATH.format(
        cik=int(cik),
        accession=accession_number.replace("-", ""),
        document=document,
    )
    return f"{base_url}{path}"


def _filing_refs(recent: Mapping[str, object], cik: str, base_url: str, url: str) -> list[FilingRef]:
    """Build a :class:`FilingRef` for every row of the submissions feed.

    The feed stores filings as parallel arrays - one array per attribute, aligned by index - so
    the columns are read together and a length mismatch is treated as a malformed response.

    Raises:
        EdgarError: If a column is missing, the columns disagree in length, or a row is unusable.
    """
    accessions = _column(recent, "accessionNumber", url)
    forms = _column(recent, "form", url)
    filing_dates = _column(recent, "filingDate", url)
    documents = _column(recent, "primaryDocument", url)
    report_dates = recent.get("reportDate")
    periods: Sequence[object] = report_dates if isinstance(report_dates, list) else []

    lengths = {len(accessions), len(forms), len(filing_dates), len(documents)}
    if len(lengths) != 1:
        raise EdgarError(f"EDGAR submissions response from {url} has mismatched filing columns: {sorted(lengths)}")

    refs: list[FilingRef] = []
    for index in range(len(accessions)):
        accession = _text(accessions[index], "accessionNumber", url)
        # A blank primaryDocument means EDGAR indexes no single main file; the complete
        # submission text file is always present under the same accession folder.
        document = documents[index] if isinstance(documents[index], str) and documents[index] else f"{accession}.txt"
        primary_document = _text(document, "primaryDocument", url)
        refs.append(
            FilingRef(
                cik=cik,
                accession_number=accession,
                form=_text(forms[index], "form", url),
                filing_date=_iso_date(filing_dates[index], "filingDate", url),
                primary_document=primary_document,
                url=_filing_url(base_url, cik, accession, primary_document),
                report_date=_optional_iso_date(periods[index]) if index < len(periods) else None,
            )
        )
    return refs
