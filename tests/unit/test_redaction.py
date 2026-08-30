"""Tests for deterministic PII redaction.

Every sample that looks like personal data is assembled by concatenation, so no email, phone,
or identifier literal ever appears in this file's source. That keeps the repository clean for
``scripts/public_safety_scan.py``, which scans ``tests/`` line by line, while still exercising
the redactor with realistically shaped input at run time.
"""

import pytest

from quorum.core.config import RedactionSettings, load_settings
from quorum.ingestion.models import REDACTION_KINDS, RedactionResult
from quorum.ingestion.redaction import Redactor

# --- samples, assembled so the literals never appear in source ---------------------------------

AREA = "5" + "55"
PREFIX = "1" + "23"
LINE = "4" + "567"

PHONE_DASHED = AREA + "-" + PREFIX + "-" + LINE
PHONE_DOTTED = AREA + "." + PREFIX + "." + LINE
PHONE_PARENS = "(" + AREA + ") " + PREFIX + "-" + LINE
PHONE_PARENS_TIGHT = "(" + AREA + ")" + PREFIX + "-" + LINE
PHONE_COUNTRY = "+1 (" + AREA + ") " + PREFIX + "-" + LINE
PHONE_TOLL_FREE = "1-" + "800" + "-" + AREA + "-" + "0199"
PHONE_INTERNATIONAL = "+44 " + "20 " + "7946" + " 0958"
PHONE_INTERNATIONAL_TIGHT = "+44" + "20" + "7946" + "0958"

EMAIL_SIMPLE = "analyst" + "@" + "example.com"
EMAIL_SUBDOMAIN = "first.last" + "@" + "ir.filings.example.org"
EMAIL_TAGGED = "desk" + "+" + "filings" + "@" + "mail.test"

SSN_DASHED = PREFIX + "-" + "45" + "-" + "6789"
SSN_SPACED = PREFIX + " " + "45" + " " + "6789"
SSN_DOTTED = PREFIX + "." + "45" + "." + "6789"

DEFAULT_SETTINGS = RedactionSettings()
EMAIL_PLACEHOLDER = DEFAULT_SETTINGS.placeholder.format(kind="email")
PHONE_PLACEHOLDER = DEFAULT_SETTINGS.placeholder.format(kind="phone")
SSN_PLACEHOLDER = DEFAULT_SETTINGS.placeholder.format(kind="ssn")


@pytest.fixture
def redactor() -> Redactor:
    return Redactor(DEFAULT_SETTINGS)


def spans(text: str, result: RedactionResult) -> list[str]:
    """Slice the ORIGINAL text with each event's offsets; each slice must be the removed PII."""
    return [text[event.start_char : event.end_char] for event in result.events]


# --- per-kind detection ------------------------------------------------------------------------


@pytest.mark.parametrize("email", [EMAIL_SIMPLE, EMAIL_SUBDOMAIN, EMAIL_TAGGED])
def test_email_addresses_are_replaced(redactor, email):
    text = f"Write to {email} for the filing."

    result = redactor.redact(text)

    assert result.text == f"Write to {EMAIL_PLACEHOLDER} for the filing."
    assert [event.kind for event in result.events] == ["email"]
    assert spans(text, result) == [email]


@pytest.mark.parametrize(
    "phone",
    [
        PHONE_DASHED,
        PHONE_DOTTED,
        PHONE_PARENS,
        PHONE_PARENS_TIGHT,
        PHONE_COUNTRY,
        PHONE_TOLL_FREE,
        PHONE_INTERNATIONAL,
        PHONE_INTERNATIONAL_TIGHT,
    ],
)
def test_phone_numbers_are_replaced(redactor, phone):
    text = f"Call {phone} weekdays."

    result = redactor.redact(text)

    assert result.text == f"Call {PHONE_PLACEHOLDER} weekdays."
    assert [event.kind for event in result.events] == ["phone"]
    assert spans(text, result) == [phone]


def test_dash_separated_ssn_is_replaced(redactor):
    text = f"Identifier {SSN_DASHED} on file."

    result = redactor.redact(text)

    assert result.text == f"Identifier {SSN_PLACEHOLDER} on file."
    assert [event.kind for event in result.events] == ["ssn"]
    assert spans(text, result) == [SSN_DASHED]


@pytest.mark.parametrize("value", [SSN_SPACED, SSN_DOTTED])
def test_space_and_dot_separated_runs_survive_by_default(redactor, value):
    """Precision bias: a tabulated figure must not be destroyed by an over-eager detector.

    Filings are dense with numbers, so a run like ``123 45 6789`` is far more likely to be three
    table columns than a social-security number. Redacting it would corrupt a financial fact
    irrecoverably, which is worse than leaving a placeholder unwritten in text that held no
    personal data. The default separator set is dash-only; see ``ingestion.redaction.ssn_separators``.
    """
    text = f"Segment totals {value} across regions."

    result = redactor.redact(text)

    assert result.text == text
    assert result.events == ()


@pytest.mark.parametrize("value", [SSN_SPACED, SSN_DOTTED])
def test_widened_separator_set_catches_other_ssn_shapes(value):
    """Corpora where SSNs genuinely use other separators can opt in through configuration."""
    redactor = Redactor(RedactionSettings(ssn_separators="-. "))
    text = f"Identifier {value} on file."

    result = redactor.redact(text)

    assert result.text == f"Identifier {SSN_PLACEHOLDER} on file."
    assert [event.kind for event in result.events] == ["ssn"]
    assert spans(text, result) == [value]


def test_every_contract_kind_is_reachable(redactor):
    text = f"{EMAIL_SIMPLE} / {PHONE_DASHED} / {SSN_DASHED}"

    result = redactor.redact(text)

    assert {event.kind for event in result.events} == REDACTION_KINDS


# --- multiple matches, ordering, offsets ---------------------------------------------------------


def test_multiple_matches_are_all_replaced(redactor):
    text = f"Desk {EMAIL_SIMPLE}, line {PHONE_PARENS}, and a second desk {EMAIL_TAGGED}."

    result = redactor.redact(text)

    expected = f"Desk {EMAIL_PLACEHOLDER}, line {PHONE_PLACEHOLDER}, and a second desk {EMAIL_PLACEHOLDER}."
    assert result.text == expected
    assert len(result.events) == 3


def test_events_are_ordered_by_start_char_not_by_detector(redactor):
    # Written SSN-first so positional ordering cannot be confused with detector precedence.
    text = f"{SSN_DASHED} then {PHONE_DASHED} then {EMAIL_SIMPLE}"

    result = redactor.redact(text)

    assert [event.kind for event in result.events] == ["ssn", "phone", "email"]
    starts = [event.start_char for event in result.events]
    assert starts == sorted(starts)


def test_event_offsets_index_the_original_text(redactor):
    text = f"Reach {EMAIL_SIMPLE} or {PHONE_DASHED}; identifier {SSN_DASHED}."

    result = redactor.redact(text)

    assert spans(text, result) == [EMAIL_SIMPLE, PHONE_DASHED, SSN_DASHED]
    assert all(event.end_char > event.start_char for event in result.events)


def test_events_never_overlap(redactor):
    text = f"{EMAIL_SUBDOMAIN} {PHONE_COUNTRY} {SSN_DOTTED} {EMAIL_TAGGED}"

    result = redactor.redact(text)

    ends = [event.end_char for event in result.events]
    starts = [event.start_char for event in result.events]
    assert all(start >= end for start, end in zip(starts[1:], ends[:-1], strict=True))


def test_surrounding_text_is_preserved_exactly(redactor):
    prefix = "Item 1A. Risk Factors\n\nInvestor relations: "
    suffix = "\n\nRevenue rose 12% to 1,234,567 units.\n"
    text = prefix + EMAIL_SIMPLE + suffix

    result = redactor.redact(text)

    assert result.text == prefix + EMAIL_PLACEHOLDER + suffix


# --- no-match behavior --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Item 1. Business",
        "Revenue was 1,234,567 in fiscal 2024.",
        "Filed on 2023-01-15 covering FY2024.",
        "Segment totals 100 200 3000 across regions.",
        "Net income rose +12.5% year over year.",
        "Forms 10-K and 8-K were filed between 2020-2024.",
        "Priced at 10 @ 5.00 per unit.",
        # Deliberate limitation: a bare digit run is a figure, never a phone number.
        "The reference number " + AREA + PREFIX + LINE + " appears in the table.",
        # Same reason: a space-separated triple carries no phone marker.
        "Row " + AREA + " " + PREFIX + " " + LINE + " of the schedule.",
    ],
)
def test_text_without_pii_is_returned_unchanged(redactor, text):
    result = redactor.redact(text)

    assert result.text == text
    assert result.events == ()


# --- disabled mode ------------------------------------------------------------------------------


def test_disabled_redactor_returns_input_untouched():
    text = f"{EMAIL_SIMPLE} {PHONE_DASHED} {SSN_DASHED}"

    result = Redactor(RedactionSettings(enabled=False)).redact(text)

    assert result.text == text
    assert result.events == ()


# --- overlap resolution -------------------------------------------------------------------------


def test_longest_match_wins_when_two_kinds_start_together(redactor):
    # The local part is itself phone-shaped, so "email" and "phone" both start at offset 0.
    text = PHONE_DASHED + "@" + "mail.test"

    result = redactor.redact(text)

    assert result.text == EMAIL_PLACEHOLDER
    assert [event.kind for event in result.events] == ["email"]
    assert spans(text, result) == [text]


def test_leftmost_match_wins_when_a_later_kind_overlaps(redactor):
    # "+1 " + an SSN-shaped tail: the phone match starts at 0 and swallows the SSN match at 3.
    tail = AREA + "-" + "12" + "-" + "3456"
    text = "+1 " + tail

    result = redactor.redact(text)

    assert result.text == PHONE_PLACEHOLDER
    assert [event.kind for event in result.events] == ["phone"]
    assert spans(text, result) == [text]


def test_no_span_is_replaced_twice(redactor):
    text = PHONE_DASHED + "@" + "mail.test"

    result = redactor.redact(text)

    assert result.text.count(EMAIL_PLACEHOLDER) == 1
    assert PHONE_PLACEHOLDER not in result.text


# --- idempotency and determinism -----------------------------------------------------------------


def test_redacting_already_redacted_text_produces_no_new_events(redactor):
    text = f"Reach {EMAIL_SIMPLE} or {PHONE_COUNTRY}; identifier {SSN_DASHED}."

    once = redactor.redact(text)
    twice = redactor.redact(once.text)

    assert twice.events == ()
    assert twice.text == once.text


def test_repeated_runs_produce_identical_results():
    text = f"{EMAIL_SUBDOMAIN} {PHONE_INTERNATIONAL} {SSN_SPACED}"

    first = Redactor(DEFAULT_SETTINGS).redact(text)
    second = Redactor(DEFAULT_SETTINGS).redact(text)

    assert first == second


# --- configuration ---------------------------------------------------------------------------------


def test_placeholder_template_comes_from_settings():
    redactor = Redactor(RedactionSettings(placeholder="<<{kind}>>"))
    text = f"{EMAIL_SIMPLE} and {PHONE_DASHED}"

    result = redactor.redact(text)

    assert result.text == "<<email>> and <<phone>>"
    assert [event.replacement for event in result.events] == ["<<email>>", "<<phone>>"]


def test_placeholder_without_a_kind_field_is_still_accepted():
    result = Redactor(RedactionSettings(placeholder="[PII]")).redact(EMAIL_SIMPLE)

    assert result.text == "[PII]"
    assert result.events[0].replacement == "[PII]"


def test_default_settings_are_read_from_config():
    configured = load_settings().ingestion.redaction

    result = Redactor().redact(EMAIL_SIMPLE)

    assert result.text == configured.placeholder.format(kind="email")
