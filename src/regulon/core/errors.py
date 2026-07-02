"""Regulon exception hierarchy.

Every Regulon-raised error derives from :class:`RegulonError` so callers can catch platform
errors without catching unrelated bugs. Subsystems add their own subclasses as they land
(gateway, retrieval, governance, ...); shared ones live here.
"""

from __future__ import annotations


class RegulonError(Exception):
    """Base class for all Regulon errors."""


class ConfigError(RegulonError):
    """Raised when configuration is missing, malformed, or inconsistent."""
