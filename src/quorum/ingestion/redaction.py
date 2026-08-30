"""Deterministic PII redaction, applied at ingest before any text reaches the store.

The redactor is a pure function of its input: identical text always yields identical replaced
text and an identical :class:`~quorum.ingestion.models.RedactionEvent` sequence, with no clock,
no randomness, and no network. That matters twice over - ingestion must be replayable, and the
audit trail has to record *where* something was removed without ever storing what it was.

Three categories are detected, matching :data:`~quorum.ingestion.models.REDACTION_KINDS`:

* ``email`` - a local part, ``@``, and a dotted domain ending in an alphabetic top-level label.
* ``phone`` - a ``+`` country-code form, a parenthesized area code, or a dash/dot delimited
  ``NNN-NNN-NNNN`` triple, each optionally preceded by a country code.
* ``ssn`` - the US social-security shape ``NNN-NN-NNNN``, written with one consistent separator
  drawn from :attr:`~quorum.core.config.RedactionSettings.ssn_separators` (dash only by default).

Precision is preferred over recall on purpose. Filings are dense with figures, so a bare run of
digits is never read as a phone number, and space-separated triples are matched only when a ``+``
country code or a parenthesized area code marks the run as a phone number. A false positive
silently corrupts a financial fact, which is worse for a retrieval corpus than a missed
placeholder in text that held no personal data to begin with. The tradeoff is documented rather
than tuned: ``5551234567`` and ``555 123 4567`` pass through untouched, and so does ``123 45 6789``
under the default SSN separator set - a space-separated run in a filing is far more likely to be
three columns of a table than a social-security number. Widen ``ssn_separators`` in
``config/quorum.yaml`` for corpora where that assumption does not hold.

Matches from different categories can overlap. They are resolved **leftmost-longest**: candidates
are ordered by start offset, then by descending length, then by a fixed category precedence, and
accepted greedily. No span is ever replaced twice, and the outcome never depends on set or
dictionary iteration order.

Redaction is idempotent for any placeholder that is not itself PII-shaped - the default
``[REDACTED:{kind}]`` carries no ``@`` and no digits, so a second pass over redacted text finds
nothing and reports no events.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from quorum.core.config import RedactionSettings, load_settings
from quorum.ingestion.models import RedactionEvent, RedactionResult

_EMAIL_PATTERN: re.Pattern[str] = re.compile(
    # Refuse to start mid-token, so a URI-prefixed address matches from its true first character
    # rather than from some arbitrary offset inside the local part.
    r"(?<![A-Za-z0-9._%+-])"
    r"[A-Za-z0-9._%+-]+"
    r"@"
    # One or more dot-terminated labels, then an alphabetic TLD; a trailing sentence period is
    # left outside the match by the greedy backtrack.
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,}"
    r"(?![A-Za-z0-9-])"
)

# A "+" country code followed by two to five digit groups: "+44 20 7946 0958", "+442079460958".
_PHONE_INTERNATIONAL = r"\+\d{1,3}(?:[ .-]?\d{2,4}){2,5}"
# A parenthesized area code marks the run as a phone number, so spaces are safe here.
_PHONE_PARENTHESIZED = r"(?:\+?\d{1,3}[ .-]?)?\(\d{3}\)[ .-]?\d{3}[ .-]?\d{4}"
# Bare triples must be dash- or dot-delimited with one consistent separator, which keeps
# tabulated figures such as "100 200 3000" out of the match set.
_PHONE_DELIMITED = r"(?:\+?\d{1,3}[ .-]?)?\d{3}(?P<phone_sep>[-.])\d{3}(?P=phone_sep)\d{4}"

_PHONE_PATTERN: re.Pattern[str] = re.compile(
    rf"(?<![\d+])(?:{_PHONE_INTERNATIONAL}|{_PHONE_PARENTHESIZED}|{_PHONE_DELIMITED})(?!\d)"
)


@lru_cache(maxsize=8)
def _ssn_pattern(separators: str) -> re.Pattern[str]:
    """Compile the SSN detector for a configured separator set.

    The separator is back-referenced, so both gaps must use the same character: a dash-dash pairing
    matches, a dash-then-dot pairing does not. Cached because the pattern depends only on
    configuration and a run reuses one separator set for every document.

    Args:
        separators: Characters accepted between SSN groups, from
            :attr:`~quorum.core.config.RedactionSettings.ssn_separators`.

    Returns:
        The compiled detector.
    """
    return re.compile(rf"(?<!\d)\d{{3}}(?P<ssn_sep>[{re.escape(separators)}])\d{{2}}(?P=ssn_sep)\d{{4}}(?!\d)")


_KINDS: tuple[str, ...] = ("email", "ssn", "phone")
"""Detector categories in precedence order; earlier entries win a tie on start offset and length."""


def _patterns(ssn_separators: str) -> tuple[tuple[str, re.Pattern[str]], ...]:
    """Return the detectors in precedence order for a configured SSN separator set."""
    return (
        ("email", _EMAIL_PATTERN),
        ("ssn", _ssn_pattern(ssn_separators)),
        ("phone", _PHONE_PATTERN),
    )


@dataclass(frozen=True, slots=True)
class _Candidate:
    """One detector hit, before overlap resolution.

    Attributes:
        start: Inclusive start offset of the hit in the input text.
        end: Exclusive end offset of the hit in the input text.
        kind: Category of the detector that produced the hit.
        precedence: Position of that detector in :func:`_patterns`; lower wins a tie.
    """

    start: int
    end: int
    kind: str
    precedence: int

    def order_key(self) -> tuple[int, int, int]:
        """Return the leftmost-longest sort key: start ascending, length descending, then precedence."""
        return (self.start, self.start - self.end, self.precedence)


def _find_candidates(text: str, ssn_separators: str) -> list[_Candidate]:
    """Return every detector hit in ``text``, ordered leftmost-longest.

    Args:
        text: Text to scan.
        ssn_separators: Characters accepted between SSN groups.

    Returns:
        All hits from all detectors, sorted so that a greedy non-overlapping walk yields the
        leftmost-longest selection.
    """
    candidates = [
        _Candidate(start=match.start(), end=match.end(), kind=kind, precedence=precedence)
        for precedence, (kind, pattern) in enumerate(_patterns(ssn_separators))
        for match in pattern.finditer(text)
    ]
    candidates.sort(key=_Candidate.order_key)
    return candidates


def _select_non_overlapping(candidates: list[_Candidate]) -> list[_Candidate]:
    """Accept candidates greedily, dropping any that overlaps an already accepted span.

    Args:
        candidates: Hits ordered by :meth:`_Candidate.order_key`.

    Returns:
        The accepted hits, in ascending start order and pairwise non-overlapping.
    """
    accepted: list[_Candidate] = []
    cursor = 0
    for candidate in candidates:
        if candidate.start < cursor:
            continue
        accepted.append(candidate)
        cursor = candidate.end
    return accepted


class Redactor:
    """Replaces email, phone, and SSN spans in text with configured placeholders.

    A redactor holds no mutable state, so one instance can be reused across a whole ingest run
    and shared freely between documents.
    """

    def __init__(self, settings: RedactionSettings | None = None) -> None:
        """Build a redactor.

        Args:
            settings: Redaction configuration. Defaults to ``ingestion.redaction`` from the
                loaded :class:`~quorum.core.config.Settings`, so thresholds and the placeholder
                template stay in ``config/quorum.yaml``.

        Raises:
            KeyError: If the configured placeholder references a field other than ``kind``.
        """
        self._settings = settings if settings is not None else load_settings().ingestion.redaction
        # Rendered once per kind: the template never varies per match, and a malformed template
        # fails here at construction rather than midway through a document.
        self._replacements: dict[str, str] = {kind: self._settings.placeholder.format(kind=kind) for kind in _KINDS}

    def redact(self, text: str) -> RedactionResult:
        """Replace every detected PII span in ``text`` with its placeholder.

        Args:
            text: Text to redact.

        Returns:
            The redacted text plus one :class:`~quorum.ingestion.models.RedactionEvent` per
            replacement, ordered by ``start_char`` ascending. Event offsets index the *original*
            ``text``, so ``text[event.start_char:event.end_char]`` is the span that was removed.
            When redaction is disabled the input is returned unchanged with no events.
        """
        if not self._settings.enabled:
            return RedactionResult(text=text, events=())

        selected = _select_non_overlapping(_find_candidates(text, self._settings.ssn_separators))
        if not selected:
            return RedactionResult(text=text, events=())

        parts: list[str] = []
        events: list[RedactionEvent] = []
        cursor = 0
        for candidate in selected:
            replacement = self._replacements[candidate.kind]
            parts.append(text[cursor : candidate.start])
            parts.append(replacement)
            events.append(
                RedactionEvent(
                    kind=candidate.kind,
                    start_char=candidate.start,
                    end_char=candidate.end,
                    replacement=replacement,
                )
            )
            cursor = candidate.end
        parts.append(text[cursor:])

        return RedactionResult(text="".join(parts), events=tuple(events))
