"""Injectable clock so time-dependent code stays deterministic in tests and evals."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """Anything that can report the current UTC time."""

    def now(self) -> datetime:
        """Return the current time as a timezone-aware UTC datetime."""
        ...


class SystemClock:
    """Real wall-clock time."""

    def now(self) -> datetime:
        """Return the current UTC time."""
        return datetime.now(UTC)


class FixedClock:
    """A clock frozen at a known instant; advance it explicitly. For tests and eval replays."""

    def __init__(self, fixed: datetime) -> None:
        """Initialize with a timezone-aware datetime.

        Raises:
            ValueError: If ``fixed`` is naive (no tzinfo).
        """
        if fixed.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")
        self._now = fixed

    def now(self) -> datetime:
        """Return the frozen instant."""
        return self._now

    def advance(self, seconds: float) -> None:
        """Move the frozen instant forward by ``seconds``."""
        self._now += timedelta(seconds=seconds)


def isoformat_utc(dt: datetime) -> str:
    """Render a timezone-aware datetime as a normalized UTC ISO-8601 string with a ``Z`` suffix.

    Raises:
        ValueError: If ``dt`` is naive (no tzinfo).
    """
    if dt.tzinfo is None:
        raise ValueError("isoformat_utc requires a timezone-aware datetime")
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")
