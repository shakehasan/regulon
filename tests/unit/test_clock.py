from datetime import UTC, datetime, timedelta, timezone

import pytest

from regulon.core.clock import Clock, FixedClock, SystemClock, isoformat_utc


def test_system_clock_returns_aware_utc():
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_fixed_clock_freezes_and_advances():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    clock = FixedClock(start)
    assert clock.now() == start
    clock.advance(90)
    assert clock.now() == start + timedelta(seconds=90)


def test_fixed_clock_rejects_naive_datetime():
    with pytest.raises(ValueError, match="timezone-aware"):
        FixedClock(datetime(2026, 1, 1))


def test_clocks_satisfy_protocol():
    def use(clock: Clock) -> datetime:
        return clock.now()

    assert use(SystemClock()).tzinfo is not None
    assert use(FixedClock(datetime(2026, 1, 1, tzinfo=UTC))).year == 2026


def test_isoformat_utc_normalizes_offset():
    dt = datetime(2026, 6, 30, 23, 30, tzinfo=timezone(timedelta(hours=-5)))
    assert isoformat_utc(dt) == "2026-07-01T04:30:00Z"


def test_isoformat_utc_rejects_naive():
    with pytest.raises(ValueError, match="timezone-aware"):
        isoformat_utc(datetime(2026, 1, 1))
