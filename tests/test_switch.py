"""Tests for the FireServiceRota response switch."""

from unittest.mock import MagicMock

import pytest

from homeassistant.components.switch import (
    DOMAIN as SWITCH_DOMAIN,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .conftest import make_incident

SWITCH = "switch.incident_response"


async def test_available_when_off_duty(
    hass: HomeAssistant, mock_api: MagicMock, setup_integration
) -> None:
    """Test the switch shows the response when off duty."""
    mock_api.get_availability.return_value = {"available": False}
    mock_api.get_latest_incident.return_value = make_incident(status="acknowledged")

    await setup_integration()

    state = hass.states.get(SWITCH)
    assert state.state == STATE_ON
    assert hass.states.get("binary_sensor.duty").state == STATE_OFF


async def test_respond_when_off_duty(
    hass: HomeAssistant, mock_api: MagicMock, setup_integration
) -> None:
    """Test responding is possible when off duty."""
    mock_api.get_availability.return_value = {"available": False}
    mock_api.get_latest_incident.return_value = make_incident()
    await setup_integration()
    assert hass.states.get(SWITCH).state == STATE_OFF

    mock_api.get_incident.return_value = make_incident(status="acknowledged")
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: SWITCH}, blocking=True
    )

    mock_api.set_incident_response.assert_called_once_with(1000, True)
    assert hass.states.get(SWITCH).state == STATE_ON

    mock_api.get_incident.return_value = make_incident(status="rejected")
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: SWITCH}, blocking=True
    )

    mock_api.set_incident_response.assert_called_with(1000, False)
    state = hass.states.get(SWITCH)
    assert state.state == STATE_OFF
    assert state.attributes["icon"] == "mdi:account-off-outline"


async def test_respond_without_incident(
    hass: HomeAssistant, mock_api: MagicMock, setup_integration
) -> None:
    """Test responding without an active incident raises an error."""
    await setup_integration()
    assert hass.states.get(SWITCH).state != STATE_UNAVAILABLE

    with pytest.raises(HomeAssistantError, match="no active incident"):
        await hass.services.async_call(
            SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: SWITCH}, blocking=True
        )
    mock_api.set_incident_response.assert_not_called()
