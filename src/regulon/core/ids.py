"""Generation of unique, time-sortable identifiers for runs, events, and records."""

from __future__ import annotations

import re
import secrets
import time

_PREFIX_RE = re.compile(r"^[a-z][a-z0-9]{0,15}$")


def new_id(prefix: str) -> str:
    """Return a unique id of the form ``<prefix>_<12-hex ms timestamp><8-hex random>``.

    Ids generated later sort lexicographically after earlier ones (millisecond resolution),
    which keeps audit logs and event streams naturally ordered.

    Args:
        prefix: Short lowercase alphanumeric namespace, e.g. ``run`` or ``evt``.

    Raises:
        ValueError: If the prefix is not lowercase alphanumeric starting with a letter.
    """
    if not _PREFIX_RE.match(prefix):
        raise ValueError(f"Invalid id prefix {prefix!r}: expected ^[a-z][a-z0-9]{{0,15}}$")
    millis = time.time_ns() // 1_000_000
    return f"{prefix}_{millis:012x}{secrets.token_hex(4)}"


def new_run_id() -> str:
    """Return a unique id for a research run."""
    return new_id("run")


def new_event_id() -> str:
    """Return a unique id for a structured event."""
    return new_id("evt")
