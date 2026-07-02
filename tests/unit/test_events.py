import pytest
from pydantic import ValidationError

from regulon.core.events import BaseEvent


def test_event_defaults():
    event = BaseEvent(event_type="test.created")
    assert event.event_id.startswith("evt_")
    assert event.occurred_at.tzinfo is not None
    assert event.run_id is None


def test_event_requires_type():
    with pytest.raises(ValidationError):
        BaseEvent()  # type: ignore[call-arg]


def test_event_is_frozen():
    event = BaseEvent(event_type="test.created", run_id="run_abc")
    with pytest.raises(ValidationError):
        event.event_type = "mutated"  # type: ignore[misc]


def test_event_serializes_round_trip():
    event = BaseEvent(event_type="test.created", run_id="run_abc")
    restored = BaseEvent.model_validate_json(event.model_dump_json())
    assert restored == event
