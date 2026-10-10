"""Tests for the FireServiceRota incident event entity."""

from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .conftest import make_incident, make_message

EVENT = "event.incident"


def _capture_events(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Collect the attributes of every fired incident event."""
    fired: list[dict[str, Any]] = []

    @callback
    def _listener(event: Event) -> None:
        if (new_state := event.data["new_state"]) and new_state.state != STATE_UNKNOWN:
            fired.append(dict(new_state.attributes))

    async_track_state_change_event(hass, EVENT, _listener)
    return fired


async def _advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    """Move time forward and run scheduled callbacks."""
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_new_and_update_events(
    hass: HomeAssistant, mock_api: MagicMock, setup_integration, send_message
) -> None:
    """Test new and update events carry the incident data."""
    await setup_integration()
    assert hass.states.get(EVENT).state == STATE_UNKNOWN
    fired = _capture_events(hass)
    incident = make_incident()
    mock_api.get_incident.return_value = incident

    await send_message(make_message(incident))
    await send_message(make_message(incident, body="Updated", trigger="new"))

    assert [event["event_type"] for event in fired] == ["new", "update"]
    assert fired[0]["id"] == 1000
    assert fired[0]["body"] == incident["body"]
    assert fired[0]["prio"] == "prio2"
    assert fired[0]["formatted_address"] == "Velvin 13\n6065BJ Montfort\n"
    assert fired[0]["can_respond_until"] == incident["can_respond_until"]
    assert fired[1]["body"] == "Updated"


async def test_response_closed_assigned(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: MagicMock,
    setup_integration,
    send_message,
) -> None:
    """Test the final assignment is announced after the response deadline."""
    await setup_integration()
    fired = _capture_events(hass)
    incident = make_incident(status="acknowledged")
    mock_api.get_incident.return_value = incident
    await send_message(make_message(incident))

    # Assigned after the last websocket message, before the deadline.
    mock_api.get_incident.return_value = make_incident(
        status="acknowledged", assigned_skill_ids=[165]
    )
    await _advance(hass, freezer, timedelta(seconds=85))
    assert [event["event_type"] for event in fired] == ["new"]

    await _advance(hass, freezer, timedelta(seconds=5))
    assert [event["event_type"] for event in fired] == ["new", "response_closed"]
    closed = fired[1]
    assert closed["id"] == 1000
    assert closed["status"] == "acknowledged"
    assert closed["acknowledged"] is True
    assert closed["assigned"] is True
    assert closed["assigned_skill_ids"] == [165]
    assert closed["reported_status"] == "dispatched"

    # Only announced once.
    await _advance(hass, freezer, timedelta(minutes=5))
    assert [event["event_type"] for event in fired] == ["new", "response_closed"]


async def test_response_closed_not_assigned(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: MagicMock,
    setup_integration,
    send_message,
) -> None:
    """Test an acknowledged response without assignment."""
    await setup_integration()
    fired = _capture_events(hass)
    incident = make_incident(status="acknowledged", assigned_skill_ids=[165])
    mock_api.get_incident.return_value = incident
    await send_message(make_message(incident))

    # Replaced by a responder with a higher priority before the deadline.
    mock_api.get_incident.return_value = make_incident(status="acknowledged")
    await _advance(hass, freezer, timedelta(seconds=90))

    closed = fired[-1]
    assert closed["event_type"] == "response_closed"
    assert closed["acknowledged"] is True
    assert closed["assigned"] is False
    assert closed["assigned_skill_ids"] == []


async def test_response_closed_uses_earliest_deadline(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: MagicMock,
    setup_integration,
    send_message,
) -> None:
    """Test the deadline is the earliest can_respond_until seen."""
    await setup_integration()
    fired = _capture_events(hass)
    first = make_incident(respond_seconds=1800)
    mock_api.get_incident.return_value = make_incident(respond_seconds=1800)
    await send_message(make_message(first))
    await send_message(make_message(make_incident(respond_seconds=80)))
    await send_message(make_message(first))

    await _advance(hass, freezer, timedelta(seconds=90))

    assert fired[-1]["event_type"] == "response_closed"
    assert fired[-1]["acknowledged"] is False


async def test_response_closed_retries_failed_fetch(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: MagicMock,
    setup_integration,
    send_message,
) -> None:
    """Test the final fetch is retried before announcing."""
    await setup_integration()
    fired = _capture_events(hass)
    incident = make_incident(status="acknowledged")
    mock_api.get_incident.return_value = incident
    await send_message(make_message(incident))

    mock_api.get_incident.return_value = None
    await _advance(hass, freezer, timedelta(seconds=90))
    assert fired[-1]["event_type"] == "new"

    mock_api.get_incident.return_value = make_incident(
        status="acknowledged", assigned_skill_ids=[165]
    )
    await _advance(hass, freezer, timedelta(seconds=10))
    assert fired[-1]["event_type"] == "response_closed"
    assert fired[-1]["assigned"] is True


async def test_response_closed_after_restart_within_deadline(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: MagicMock,
    setup_integration,
) -> None:
    """Test the deadline is still announced when HA starts before it."""
    incident = make_incident(
        status="acknowledged",
        assigned_skill_ids=[165],
        created_at=dt_util.utcnow() - timedelta(seconds=30),
    )
    mock_api.get_latest_incident.return_value = incident
    mock_api.get_incident.return_value = incident
    await setup_integration()
    fired = _capture_events(hass)

    await _advance(hass, freezer, timedelta(seconds=60))

    assert [event["event_type"] for event in fired] == ["response_closed"]
    assert fired[0]["assigned"] is True


async def test_no_response_closed_after_restart_past_deadline(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: MagicMock,
    setup_integration,
) -> None:
    """Test no event is fired for a deadline that passed before startup."""
    incident = make_incident(
        status="acknowledged", created_at=dt_util.utcnow() - timedelta(minutes=5)
    )
    mock_api.get_latest_incident.return_value = incident
    mock_api.get_incident.return_value = incident
    await setup_integration()
    fired = _capture_events(hass)

    await _advance(hass, freezer, timedelta(minutes=2))

    assert fired == []


async def test_ended_event(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: MagicMock,
    setup_integration,
    send_message,
) -> None:
    """Test an ended event is fired when the incident finishes."""
    await setup_integration()
    fired = _capture_events(hass)
    incident = make_incident()
    mock_api.get_incident.return_value = incident
    await send_message(make_message(incident))

    mock_api.get_incident.return_value = make_incident(state="finished")
    await _advance(hass, freezer, timedelta(seconds=30))

    ended = fired[-1]
    assert ended["event_type"] == "ended"
    assert ended["id"] == 1000
    assert ended["state"] == "finished"
