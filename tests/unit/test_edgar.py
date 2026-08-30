"""Tests for the public SEC EDGAR client.

Every test drives the client through an injected fake opener and a fake timeline, so the suite
never opens a socket and never sleeps. All company names, tickers, CIKs, and accession numbers
below are invented placeholders, not real filers.
"""

import json
from datetime import date
from urllib.error import HTTPError, URLError

import pytest

from quorum.core.config import EdgarSettings
from quorum.ingestion import EdgarError
from quorum.ingestion import edgar as edgar_module
from quorum.ingestion.edgar import EdgarClient, FilingRef, urllib_opener

CIK_DIGITS = "1234567"
PADDED_CIK = "0001234567"
TICKER = "EXMP"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK0001234567.json"


class FakeOpener:
    """Serves canned responses in order, recording every request. The last response repeats."""

    def __init__(self, *responses: bytes | Exception) -> None:
        """Queue the bodies (or exceptions) to serve, in request order."""
        self.responses: list[bytes | Exception] = list(responses) or [b""]
        self.requests: list[tuple[str, dict[str, str], float]] = []

    def __call__(self, request_url: str, headers, timeout: float) -> bytes:
        self.requests.append((request_url, dict(headers), timeout))
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return response

    @property
    def urls(self) -> list[str]:
        return [request[0] for request in self.requests]


class FakeTimeline:
    """A monotonic clock whose sleep advances time instead of blocking."""

    def __init__(self, start: float = 100.0) -> None:
        """Start the timeline at an arbitrary monotonic instant."""
        self.now = start
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeResponse:
    """Minimal stand-in for the object urlopen returns."""

    def __init__(self, body: bytes) -> None:
        """Hold the body the fake response will return."""
        self.body = body

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> "FakeResponse":
        """Enter the context manager, as urlopen's return value does."""
        return self

    def __exit__(self, *_exc: object) -> bool:
        """Leave the context manager without suppressing exceptions."""
        return False


def build_settings(**overrides) -> EdgarSettings:
    fields = {
        "user_agent": "quorum-unit-tests (offline)",
        "base_url": "https://www.sec.gov",
        "request_timeout_seconds": 7.5,
        "min_request_interval_seconds": 0.0,
    }
    fields.update(overrides)
    return EdgarSettings(**fields)


def build_client(opener: FakeOpener, timeline: FakeTimeline | None = None, **setting_overrides) -> EdgarClient:
    clock = timeline or FakeTimeline()
    return EdgarClient(
        build_settings(**setting_overrides),
        opener=opener,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )


def as_bytes(payload: object) -> bytes:
    return json.dumps(payload).encode("utf-8")


def tickers_payload() -> bytes:
    return as_bytes(
        {
            "0": {"cik_str": int(CIK_DIGITS), "ticker": TICKER, "title": "Example Placeholder Corp"},
            "1": {"cik_str": 7654321, "ticker": "OTHR", "title": "Other Placeholder Inc"},
        }
    )


def submissions_payload(rows: list[dict[str, object]], **column_overrides: object) -> bytes:
    recent: dict[str, object] = {
        "accessionNumber": [row["accession"] for row in rows],
        "form": [row["form"] for row in rows],
        "filingDate": [row["filed"] for row in rows],
        "primaryDocument": [row.get("doc", "primary.htm") for row in rows],
        "reportDate": [row.get("report", "") for row in rows],
    }
    recent.update(column_overrides)
    return as_bytes({"cik": CIK_DIGITS, "filings": {"recent": recent}})


def annual_rows() -> list[dict[str, object]]:
    return [
        {
            "accession": "0001234567-24-000012",
            "form": "10-K",
            "filed": "2024-11-01",
            "report": "2024-09-28",
            "doc": "exmp-20240928.htm",
        },
        {"accession": "0001234567-24-000009", "form": "10-Q", "filed": "2024-08-02", "doc": "q3.htm"},
        {
            "accession": "0001234567-23-000031",
            "form": "10-K",
            "filed": "2023-11-03",
            "report": "2023-09-30",
            "doc": "exmp-20230930.htm",
        },
        {"accession": "0001234567-22-000027", "form": "10-K/A", "filed": "2022-12-01", "doc": "amended.htm"},
    ]


def build_ref(**overrides) -> FilingRef:
    fields = {
        "cik": PADDED_CIK,
        "accession_number": "0001234567-24-000012",
        "form": "10-K",
        "filing_date": date(2024, 11, 1),
        "primary_document": "exmp-20240928.htm",
        "url": "https://www.sec.gov/Archives/edgar/data/1234567/000123456724000012/exmp-20240928.htm",
    }
    fields.update(overrides)
    return FilingRef(**fields)


# --- construction -------------------------------------------------------------------------


def test_blank_user_agent_is_rejected_at_construction():
    with pytest.raises(ValueError, match="User-Agent"):
        EdgarClient(build_settings(user_agent="   "), opener=FakeOpener())


def test_client_falls_back_to_project_settings():
    client = EdgarClient(opener=FakeOpener(tickers_payload()))
    assert client.lookup_cik(TICKER) == PADDED_CIK


# --- lookup_cik ---------------------------------------------------------------------------


def test_lookup_cik_zero_pads_to_ten_digits():
    client = build_client(FakeOpener(tickers_payload()))
    assert client.lookup_cik(TICKER) == PADDED_CIK


def test_lookup_cik_ignores_case_and_surrounding_whitespace():
    client = build_client(FakeOpener(tickers_payload()))
    assert client.lookup_cik("  exmp \n") == PADDED_CIK


def test_lookup_cik_requests_the_company_index_with_the_configured_user_agent():
    opener = FakeOpener(tickers_payload())
    build_client(opener).lookup_cik(TICKER)

    url, headers, timeout = opener.requests[0]
    assert url == TICKERS_URL
    assert headers["User-Agent"] == "quorum-unit-tests (offline)"
    assert headers["Accept-Encoding"] == "identity"
    assert timeout == 7.5


def test_lookup_cik_blank_ticker_raises_value_error():
    with pytest.raises(ValueError, match="ticker must not be blank"):
        build_client(FakeOpener(tickers_payload())).lookup_cik("   ")


def test_lookup_cik_unknown_ticker_raises_edgar_error():
    client = build_client(FakeOpener(tickers_payload()))
    with pytest.raises(EdgarError, match="no ticker NOPE"):
        client.lookup_cik("nope")


def test_lookup_cik_skips_malformed_index_entries():
    payload = as_bytes({"0": "not-an-object", "1": {"ticker": TICKER, "cik_str": int(CIK_DIGITS)}})
    assert build_client(FakeOpener(payload)).lookup_cik(TICKER) == PADDED_CIK


def test_lookup_cik_unusable_cik_in_index_raises_edgar_error():
    payload = as_bytes({"0": {"ticker": TICKER, "cik_str": "not-a-number"}})
    with pytest.raises(EdgarError, match="unusable CIK"):
        build_client(FakeOpener(payload)).lookup_cik(TICKER)


# --- list_filings -------------------------------------------------------------------------


def test_list_filings_queries_the_data_host_with_a_padded_cik():
    opener = FakeOpener(submissions_payload(annual_rows()))
    build_client(opener).list_filings(CIK_DIGITS, "10-K")
    assert opener.urls == [SUBMISSIONS_URL]


def test_list_filings_accepts_a_prefixed_cik():
    opener = FakeOpener(submissions_payload(annual_rows()))
    build_client(opener).list_filings("CIK0001234567", "10-K")
    assert opener.urls == [SUBMISSIONS_URL]


def test_list_filings_keeps_only_the_requested_form():
    refs = build_client(FakeOpener(submissions_payload(annual_rows()))).list_filings(PADDED_CIK, "10-k")
    assert [ref.form for ref in refs] == ["10-K", "10-K"]
    assert all(ref.accession_number != "0001234567-22-000027" for ref in refs)


def test_list_filings_returns_newest_first_regardless_of_feed_order():
    rows = list(reversed(annual_rows()))
    refs = build_client(FakeOpener(submissions_payload(rows))).list_filings(PADDED_CIK, "10-K")
    assert [ref.filing_date for ref in refs] == [date(2024, 11, 1), date(2023, 11, 3)]


def test_list_filings_applies_the_limit():
    refs = build_client(FakeOpener(submissions_payload(annual_rows()))).list_filings(PADDED_CIK, "10-K", limit=1)
    assert [ref.accession_number for ref in refs] == ["0001234567-24-000012"]


def test_list_filings_builds_the_archive_url_from_an_unpadded_cik():
    refs = build_client(FakeOpener(submissions_payload(annual_rows()))).list_filings(PADDED_CIK, "10-K", limit=1)
    assert refs[0].url == ("https://www.sec.gov/Archives/edgar/data/1234567/000123456724000012/exmp-20240928.htm")


def test_list_filings_uses_the_report_period_for_the_fiscal_year():
    refs = build_client(FakeOpener(submissions_payload(annual_rows()))).list_filings(PADDED_CIK, "10-K", limit=1)
    assert refs[0].report_date == date(2024, 9, 28)
    assert refs[0].fiscal_year == 2024


def test_fiscal_year_falls_back_to_the_filing_year_without_a_report_period():
    rows = [{"accession": "0001234567-25-000004", "form": "10-K", "filed": "2025-01-31", "report": ""}]
    refs = build_client(FakeOpener(submissions_payload(rows))).list_filings(PADDED_CIK, "10-K")
    assert refs[0].report_date is None
    assert refs[0].fiscal_year == 2025


def test_list_filings_tolerates_an_unparsable_report_period():
    rows = [{"accession": "0001234567-25-000004", "form": "10-K", "filed": "2025-01-31", "report": "not-a-date"}]
    refs = build_client(FakeOpener(submissions_payload(rows))).list_filings(PADDED_CIK, "10-K")
    assert refs[0].report_date is None


def test_list_filings_tolerates_a_missing_report_column():
    rows = [{"accession": "0001234567-25-000004", "form": "10-K", "filed": "2025-01-31"}]
    payload = submissions_payload(rows, reportDate="unexpected-scalar")
    refs = build_client(FakeOpener(payload)).list_filings(PADDED_CIK, "10-K")
    assert refs[0].report_date is None


def test_list_filings_falls_back_to_the_complete_submission_file():
    rows = [{"accession": "0001234567-25-000004", "form": "10-K", "filed": "2025-01-31", "doc": ""}]
    refs = build_client(FakeOpener(submissions_payload(rows))).list_filings(PADDED_CIK, "10-K")
    assert refs[0].primary_document == "0001234567-25-000004.txt"
    assert refs[0].url.endswith("/000123456725000004/0001234567-25-000004.txt")


def test_list_filings_returns_an_empty_list_when_no_filing_matches():
    assert build_client(FakeOpener(submissions_payload(annual_rows()))).list_filings(PADDED_CIK, "8-K") == []


@pytest.mark.parametrize(
    ("cik", "form", "limit", "message"),
    [
        (PADDED_CIK, "10-K", 0, "limit must be at least 1"),
        (PADDED_CIK, "  ", 5, "form must not be blank"),
        ("not-a-cik", "10-K", 5, "is not a CIK"),
        ("12345678901", "10-K", 5, "is not a CIK"),
    ],
)
def test_list_filings_rejects_invalid_arguments(cik, form, limit, message):
    opener = FakeOpener(submissions_payload(annual_rows()))
    with pytest.raises(ValueError, match=message):
        build_client(opener).list_filings(cik, form, limit=limit)
    assert opener.requests == []


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (as_bytes({"cik": CIK_DIGITS}), "no 'filings' object"),
        (as_bytes({"filings": {"other": {}}}), "no 'filings.recent' object"),
        (as_bytes({"filings": {"recent": {"form": []}}}), "missing the 'accessionNumber' column"),
    ],
)
def test_list_filings_rejects_a_malformed_submissions_response(payload, message):
    with pytest.raises(EdgarError, match=message):
        build_client(FakeOpener(payload)).list_filings(PADDED_CIK, "10-K")


def test_list_filings_rejects_mismatched_columns():
    payload = submissions_payload(annual_rows(), form=["10-K"])
    with pytest.raises(EdgarError, match="mismatched filing columns"):
        build_client(FakeOpener(payload)).list_filings(PADDED_CIK, "10-K")


def test_list_filings_rejects_an_unparsable_filing_date():
    rows = [{"accession": "0001234567-25-000004", "form": "10-K", "filed": "31 January 2025"}]
    with pytest.raises(EdgarError, match="unparsable filingDate"):
        build_client(FakeOpener(submissions_payload(rows))).list_filings(PADDED_CIK, "10-K")


def test_list_filings_rejects_a_non_string_form():
    rows = [{"accession": "0001234567-25-000004", "form": 10, "filed": "2025-01-31"}]
    with pytest.raises(EdgarError, match="non-string form"):
        build_client(FakeOpener(submissions_payload(rows))).list_filings(PADDED_CIK, "10-K")


def test_list_filings_honours_a_non_www_base_url():
    opener = FakeOpener(submissions_payload(annual_rows()))
    client = build_client(opener, base_url="https://edgar.test/")
    refs = client.list_filings(PADDED_CIK, "10-K", limit=1)
    assert opener.urls == ["https://edgar.test/submissions/CIK0001234567.json"]
    assert refs[0].url.startswith("https://edgar.test/Archives/edgar/data/1234567/")


# --- fetch_filing_text --------------------------------------------------------------------


def test_fetch_filing_text_returns_the_document_body():
    opener = FakeOpener(b"<html>Item 1. Business</html>")
    assert build_client(opener).fetch_filing_text(build_ref()) == "<html>Item 1. Business</html>"
    assert opener.urls == [build_ref().url]


def test_fetch_filing_text_falls_back_to_the_legacy_encoding():
    opener = FakeOpener(b"Net revenue rose 3\xa0% in caf\xe9 operations")
    text = build_client(opener).fetch_filing_text(build_ref())
    assert "café operations" in text


# --- failure wrapping ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (HTTPError("https://www.sec.gov/files", 403, "Forbidden", {}, None), "HTTP 403"),
        (URLError("name resolution failed"), "name resolution failed"),
        (TimeoutError("the read operation timed out"), "timed out"),
        (ValueError("unknown url type"), "unknown url type"),
    ],
)
def test_transport_failures_are_wrapped_in_edgar_error(error, message):
    client = build_client(FakeOpener(error))
    with pytest.raises(EdgarError, match=message) as raised:
        client.lookup_cik(TICKER)
    assert TICKERS_URL in str(raised.value)
    assert raised.value.__cause__ is error


def test_an_edgar_error_from_the_opener_is_not_rewrapped():
    original = EdgarError("refusing to open a non-http URL")
    with pytest.raises(EdgarError, match="refusing to open") as raised:
        build_client(FakeOpener(original)).lookup_cik(TICKER)
    assert raised.value is original


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"{not json", "unreadable JSON"),
        (b"\xff\xfe{}", "unreadable JSON"),
        (b"[1, 2, 3]", "expected a JSON object"),
    ],
)
def test_malformed_json_is_wrapped_in_edgar_error(body, message):
    with pytest.raises(EdgarError, match=message):
        build_client(FakeOpener(body)).lookup_cik(TICKER)


# --- rate limiting ------------------------------------------------------------------------


def test_first_request_is_not_delayed():
    timeline = FakeTimeline()
    client = build_client(FakeOpener(tickers_payload()), timeline, min_request_interval_seconds=0.25)
    client.lookup_cik(TICKER)
    assert timeline.slept == []


def test_consecutive_requests_are_spaced_by_the_configured_interval():
    timeline = FakeTimeline()
    opener = FakeOpener(tickers_payload())
    client = build_client(opener, timeline, min_request_interval_seconds=0.25)

    client.lookup_cik(TICKER)
    client.lookup_cik(TICKER)

    assert timeline.slept == [0.25]
    assert len(opener.requests) == 2


def test_no_delay_once_the_interval_has_already_elapsed():
    timeline = FakeTimeline()
    client = build_client(FakeOpener(tickers_payload()), timeline, min_request_interval_seconds=0.25)

    client.lookup_cik(TICKER)
    timeline.advance(1.0)
    client.lookup_cik(TICKER)

    assert timeline.slept == []


def test_rate_limiting_is_skipped_when_the_interval_is_zero():
    timeline = FakeTimeline()
    client = build_client(FakeOpener(tickers_payload()), timeline, min_request_interval_seconds=0.0)

    client.lookup_cik(TICKER)
    client.lookup_cik(TICKER)

    assert timeline.slept == []


# --- the default urllib opener ------------------------------------------------------------


def test_urllib_opener_refuses_a_non_http_scheme():
    with pytest.raises(EdgarError, match="http or https"):
        urllib_opener("file:///etc/passwd", {"User-Agent": "quorum-unit-tests"}, 1.0)


def test_urllib_opener_sends_the_headers_and_timeout(monkeypatch):
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["user_agent"] = request.get_header("User-agent")
        captured["timeout"] = timeout
        return FakeResponse(b"filing bytes")

    monkeypatch.setattr(edgar_module, "urlopen", fake_urlopen)

    body = urllib_opener(TICKERS_URL, {"User-Agent": "quorum-unit-tests"}, 3.0)

    assert body == b"filing bytes"
    assert captured == {"url": TICKERS_URL, "user_agent": "quorum-unit-tests", "timeout": 3.0}


def test_client_uses_the_urllib_opener_by_default(monkeypatch):
    monkeypatch.setattr(edgar_module, "urlopen", lambda request, timeout: FakeResponse(b"Item 1. Business"))
    client = EdgarClient(build_settings())
    assert client.fetch_filing_text(build_ref()) == "Item 1. Business"
