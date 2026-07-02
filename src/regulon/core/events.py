"""Structured event base model.

Every meaningful action in Regulon (model call, route decision, retrieval, guardrail verdict,
approval) is emitted as a typed event. Subsystems subclass :class:`BaseEvent` with their own
payload fields; this module defines only the shared envelope.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from regulon.core.ids import new_event_id


def _utc_now() -> datetime:
    return datetime.now(UTC)


class BaseEvent(BaseModel):
    """Common envelope for all structured events."""

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(default_factory=new_event_id)
    event_type: str
    occurred_at: datetime = Field(default_factory=_utc_now)
    run_id: str | None = None
