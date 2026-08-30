"""Quorum exception hierarchy.

Every Quorum-raised error derives from :class:`QuorumError` so callers can catch platform
errors without catching unrelated bugs. Subsystems add their own subclasses as they land
(gateway, retrieval, governance, ...); shared ones live here.
"""

from __future__ import annotations


class QuorumError(Exception):
    """Base class for all Quorum errors."""


class ConfigError(QuorumError):
    """Raised when configuration is missing, malformed, or inconsistent."""
