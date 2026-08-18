"""Client for the free, public SEC EDGAR APIs.

EDGAR publishes every filing with no account, no key, and no metering, through three endpoints
this module wraps with the standard library alone: the company index (``company_tickers.json``),
a per-company submissions feed, and the filing documents under ``/Archives``.

Two rules shape the design:

* **Every byte arrives through an injected** :class:`UrlOpener`. :func:`urllib_opener` is the only
  code that calls ``urllib``, so tests drive the client with canned bytes and never open a socket.
* **Every caller identifies itself and waits its turn.** SEC asks automated clients for a
  descriptive ``User-Agent`` and a modest request rate, so each request carries
  :attr:`~regulon.core.config.EdgarSettings.user_agent` and is spaced by at least
  :attr:`~regulon.core.config.EdgarSettings.min_request_interval_seconds`.

Failure handling follows one convention: anything that goes wrong with the remote side - a
transport error, a bad status, malformed JSON, a missing field - surfaces as
:class:`~regulon.ingestion.errors.EdgarError` naming the URL, never as a raw ``URLError`` or
``JSONDecodeError``. Arguments that are wrong before any request is made (a blank ticker, a
non-numeric CIK, ``limit < 1``) raise ``ValueError``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field

from regulon.core.config import EdgarSettings, Settings
from regulon.ingestion.errors import EdgarError

# Endpoint layout of the public EDGAR API. These are not tunables: they describe how SEC
# publishes its data, so they live beside the code that builds the URLs. Only the host, the
# timeout, the rate limit, and the User-Agent are configurable (config/regulon.yaml).
_TICKERS_PATH = "/files/company_tickers.json"
_SUBMISSIONS_PATH = "/submissions/CIK{cik}.json"
_ARCHIVES_PATH = "/Archives/edgar/data/{cik}/{accession}/{document}"
_CIK_DIGITS = 10
_WWW_LABEL = "www."
_DATA_LABEL = "data."
_ALLOWED_SCHEMES = frozenset({"http", "https"})
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


class UrlOpener(Protocol):
    """Performs one HTTP GET and returns the raw response body.

    The parameters are positional-only so any three-argument callable - a plain function, a
    method, a test fake - satisfies the protocol regardless of how it names them.
    """

    def __call__(self, request_url: str, headers: Mapping[str, str], timeout: float, /) -> bytes:
        """Fetch ``request_url`` with ``headers``, giving up after ``timeout`` seconds."""
        ...


def urllib_opener(request_url: str, headers: Mapping[str, str], timeout: float, /) -> bytes:
    """Fetch a URL with :mod:`urllib` - the default, and the only networked code in this module.

    Args:
        request_url: Absolute ``http``/``https`` URL to GET.
        headers: Request headers to send verbatim.
        timeout: Socket timeout in seconds.

    Returns:
        The response body as raw bytes.

    Raises:
        EdgarError: If the URL uses a scheme other than ``http`` or ``https``; refusing other
            schemes keeps a misconfigured base URL from turning into a local file read.
    """
    scheme = urlsplit(request_url).scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise EdgarError(f"refusing to open {request_url!r}: EDGAR requests must use http or https")
    request = Request(request_url, headers=dict(headers), method="GET")
    with urlopen(request, timeout=timeout) as response:  # Scheme restricted to http/https above.
        body: bytes = response.read()
    return body


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


class EdgarClient:
    """Reads companies, filing lists, and filing text from the public EDGAR endpoints.

    The client is stateful only in its rate limiter, and every request it makes goes through the
    opener it was constructed with, so a test can supply canned responses and assert on the exact
    URLs and headers requested.
    """

    def __init__(
        self,
        settings: EdgarSettings | None = None,
        *,
        opener: UrlOpener | None = None,
        sleep: Callable[[float], None] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        """Initialize the client.

        Args:
            settings: EDGAR settings; defaults to ``Settings().ingestion.edgar``.
            opener: Performs the HTTP GETs; defaults to :func:`urllib_opener`. Tests inject a fake.
            sleep: Blocks for the given seconds; defaults to :func:`time.sleep`. Tests inject a
                recorder so the rate limit is asserted without spending real time.
            monotonic: Returns a monotonic timestamp in seconds; defaults to :func:`time.monotonic`.

        Raises:
            ValueError: If the configured User-Agent is blank. SEC rejects unidentified callers,
                so this fails at construction rather than as a puzzling 403 later.
        """
        self._settings = settings if settings is not None else Settings().ingestion.edgar
        if not self._settings.user_agent.strip():
            raise ValueError("EDGAR requires a descriptive User-Agent; set ingestion.edgar.user_agent")
        self._opener: UrlOpener = opener if opener is not None else urllib_opener
        self._sleep: Callable[[float], None] = sleep if sleep is not None else time.sleep
        self._monotonic: Callable[[], float] = monotonic if monotonic is not None else time.monotonic
        self._base_url = self._settings.base_url.rstrip("/")
        self._submissions_base_url = _submissions_base_url(self._base_url)
        self._last_request_at: float | None = None

    def lookup_cik(self, ticker: str) -> str:
        """Resolve a ticker symbol to its zero-padded ten-digit CIK.

        Args:
            ticker: Ticker symbol; matched case-insensitively after stripping whitespace.

        Returns:
            The CIK as ten digits, e.g. ``"0000320193"``.

        Raises:
            ValueError: If ``ticker`` is blank.
            EdgarError: If the company index cannot be fetched or parsed, or lists no such ticker.
        """
        wanted = ticker.strip().upper()
        if not wanted:
            raise ValueError("ticker must not be blank")

        url = f"{self._base_url}{_TICKERS_PATH}"
        payload = self._get_json(url)
        for entry in payload.values():
            if not isinstance(entry, Mapping):
                continue
            symbol = entry.get("ticker")
            if not isinstance(symbol, str) or symbol.strip().upper() != wanted:
                continue
            try:
                return _normalize_cik(entry.get("cik_str"))
            except ValueError as exc:
                raise EdgarError(f"EDGAR company index lists an unusable CIK for {wanted}: {exc}") from exc
        raise EdgarError(f"EDGAR company index lists no ticker {wanted}")

    def list_filings(self, cik: str, form: str, *, limit: int = 5) -> list[FilingRef]:
        """List a company's most recent filings of one form type, newest first.

        Args:
            cik: Central Index Key, padded or not, with or without a ``CIK`` prefix.
            form: Form type to keep, matched case-insensitively against EDGAR's own label, so
                ``10-K`` does not match an amended ``10-K/A``.
            limit: Maximum number of filings to return.

        Returns:
            Up to ``limit`` filing references ordered by filing date descending, ties broken by
            accession number descending so the order is deterministic.

        Raises:
            ValueError: If ``cik`` is not numeric, ``form`` is blank, or ``limit`` is below 1.
            EdgarError: If the submissions feed cannot be fetched or parsed.
        """
        if limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit}")
        wanted_form = form.strip().upper()
        if not wanted_form:
            raise ValueError("form must not be blank")
        padded_cik = _normalize_cik(cik)

        url = f"{self._submissions_base_url}{_SUBMISSIONS_PATH.format(cik=padded_cik)}"
        payload = self._get_json(url)
        filings = _mapping(payload.get("filings"), "'filings'", url)
        recent = _mapping(filings.get("recent"), "'filings.recent'", url)

        refs = [ref for ref in _filing_refs(recent, padded_cik, self._base_url, url) if ref.form.upper() == wanted_form]
        refs.sort(key=lambda ref: (ref.filing_date, ref.accession_number), reverse=True)
        return refs[:limit]

    def fetch_filing_text(self, ref: FilingRef) -> str:
        """Download one filing document and return it as text.

        The bytes are returned as EDGAR served them - HTML filings arrive as HTML - so parsing is
        the caller's job. Decoding never fails: documents that are not UTF-8 fall back to the
        legacy encoding older filings use.

        Args:
            ref: Filing reference produced by :meth:`list_filings`.

        Returns:
            The document's text.

        Raises:
            EdgarError: If the document cannot be fetched.
        """
        return _decode(self._get_bytes(ref.url))

    def _headers(self) -> dict[str, str]:
        """Build the headers sent on every request.

        ``Accept-Encoding: identity`` is deliberate: ``urllib`` does not transparently decompress,
        so asking for an uncompressed body keeps the opener contract (raw bytes) honest.
        """
        return {"User-Agent": self._settings.user_agent, "Accept-Encoding": "identity"}

    def _throttle(self) -> None:
        """Wait, if needed, so consecutive requests stay at least the configured interval apart."""
        interval = self._settings.min_request_interval_seconds
        if interval <= 0:
            return
        if self._last_request_at is not None:
            remaining = interval - (self._monotonic() - self._last_request_at)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_at = self._monotonic()

    def _get_bytes(self, url: str) -> bytes:
        """Fetch one URL through the injected opener, wrapping every transport failure.

        Raises:
            EdgarError: On any transport failure, bad status, or timeout.
        """
        self._throttle()
        try:
            return self._opener(url, self._headers(), self._settings.request_timeout_seconds)
        except EdgarError:
            raise
        except HTTPError as exc:
            raise EdgarError(f"EDGAR request to {url} failed with HTTP {exc.code} {exc.reason}") from exc
        except URLError as exc:
            raise EdgarError(f"EDGAR request to {url} failed: {exc.reason}") from exc
        except (OSError, ValueError) as exc:
            raise EdgarError(f"EDGAR request to {url} failed: {exc}") from exc

    def _get_json(self, url: str) -> Mapping[str, object]:
        """Fetch one URL and parse the body as a JSON object.

        Raises:
            EdgarError: On any transport failure or malformed response body.
        """
        return _json_object(self._get_bytes(url), url)
