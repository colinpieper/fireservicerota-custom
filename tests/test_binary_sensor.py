"""Tests for the FireServiceRota incident binary sensors."""

from datetime import timedelta
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant

from .conftest import make_incident, make_message

ACTIVE = "binary_sensor.incident_active"
ASSIGNED = "binary_sensor.incident_assigned"


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

    freezer.tick(timedelta(hours=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    # The last incident's assignment stays shown after it is no longer active.
    assert hass.states.get(ACTIVE).state == STATE_OFF
    assert hass.states.get(ACTIVE).attributes.get("incident_id") is None
    assert hass.states.get(ASSIGNED).state == STATE_ON
