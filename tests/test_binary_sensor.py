"""Tests for the FireServiceRota binary sensors."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import make_availability, make_incident, make_message

ACTIVE = "binary_sensor.incident_active"
ASSIGNED = "binary_sensor.incident_assigned"
DUTY = "binary_sensor.duty"


async def test_duty_available(hass: HomeAssistant, setup_integration) -> None:
    """Test the duty sensor shows the current and next period."""
    await setup_integration()

    state = hass.states.get(DUTY)
    assert state.state == STATE_ON
    assert dict(state.attributes) == {
        "friendly_name": "Duty",
        "start_time": "2026-10-01T08:00:00.000+02:00",
        "end_time": "2026-10-01T14:00:00.000+02:00",
        "available": True,
        "active": True,
        "assigned_function_ids": [],
        "skill_ids": [165],
        "type": "exception",
        "next_start_time": "2026-10-01T14:00:00.000+02:00",
        "next_end_time": "2026-10-01T18:45:00.000+02:00",
        "next_available": False,
        "next_type": "recurring",
    }


async def test_duty_unavailable(
    hass: HomeAssistant, mock_api: MagicMock, setup_integration
) -> None:
    """Test the duty sensor shows the period while unavailable."""
    mock_api.get_availability.return_value = make_availability(
        datetime.fromisoformat("2026-10-01T15:00:00+02:00")
    )
    await setup_integration()

    state = hass.states.get(DUTY)
    assert state.state == STATE_OFF
    assert state.attributes["start_time"] == "2026-10-01T14:00:00.000+02:00"
    assert state.attributes["end_time"] == "2026-10-01T18:45:00.000+02:00"
    assert state.attributes["available"] is False
    assert state.attributes["type"] == "recurring"
    assert state.attributes["next_start_time"] == "2026-10-01T18:45:00.000+02:00"
    assert state.attributes["next_end_time"] == "2026-10-02T06:00:00.000+02:00"
    assert state.attributes["next_available"] is True
    assert state.attributes["next_type"] == "recurring"
    assert "schedule" not in state.attributes


async def test_no_incident(hass: HomeAssistant, setup_integration) -> None:
    """Test the binary sensors without an incident."""
    await setup_integration()

    assert hass.states.get(ACTIVE).state == STATE_OFF
    assert hass.states.get(ASSIGNED).state == STATE_UNKNOWN


async def test_assignment_changes(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: MagicMock,
    setup_integration,
    send_message,
) -> None:
    """Test the assigned sensor follows the response."""
    await setup_integration()
    incident = make_incident(status="acknowledged")
    mock_api.get_incident.return_value = incident
    await send_message(make_message(incident))

    assert hass.states.get(ACTIVE).state == STATE_ON
    assert hass.states.get(ACTIVE).attributes["incident_id"] == 1000
    assert hass.states.get(ASSIGNED).state == STATE_OFF

    mock_api.get_incident.return_value = make_incident(
        status="acknowledged", assigned_skill_ids=[165]
    )
    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    state = hass.states.get(ASSIGNED)
    assert state.state == STATE_ON
    assert state.attributes["assigned_skill_ids"] == [165]
    assert state.attributes["suggested_skill_ids"] == [165]
    assert state.attributes["status"] == "acknowledged"

    mock_api.get_incident.return_value = make_incident(
        status="acknowledged",
        assigned_skill_ids=[165],
        end_time=dt_util.utcnow(),
    )
    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    # The last incident's assignment stays shown after it is no longer active.
    assert hass.states.get(ACTIVE).state == STATE_OFF
    assert hass.states.get(ACTIVE).attributes.get("incident_id") is None
    assert hass.states.get(ASSIGNED).state == STATE_ON
