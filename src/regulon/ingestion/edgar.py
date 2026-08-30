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

import time
from collections.abc import Callable, Mapping
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from regulon.core.config import EdgarSettings, Settings
from regulon.ingestion.edgar_parsing import (
    FilingRef,
    _decode,
    _filing_refs,
    _json_object,
    _mapping,
    _normalize_cik,
    _submissions_base_url,
)
from regulon.ingestion.errors import EdgarError

__all__ = ["EdgarClient", "FilingRef", "UrlOpener", "urllib_opener"]
"""Public surface of the EDGAR client.

:class:`FilingRef` is defined in :mod:`regulon.ingestion.edgar_parsing` and re-exported here so
callers have one import site for the client and the type it returns.
"""

# Endpoint layout of the public EDGAR API. These are not tunables: they describe how SEC
# publishes its data, so they live beside the code that builds the URLs. Only the host, the
# timeout, the rate limit, and the User-Agent are configurable (config/regulon.yaml).
_TICKERS_PATH = "/files/company_tickers.json"
_SUBMISSIONS_PATH = "/submissions/CIK{cik}.json"
_ALLOWED_SCHEMES = frozenset({"http", "https"})


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
