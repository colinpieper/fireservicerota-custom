"""Tests for FireServiceRota diagnostics."""

from unittest.mock import MagicMock

from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from homeassistant.components.diagnostics import REDACTED
from homeassistant.core import HomeAssistant

from .conftest import make_incident


async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_api: MagicMock,
    setup_integration,
) -> None:
    """Test diagnostics are redacted."""
    mock_api.get_latest_incident.return_value = make_incident(status="acknowledged")
    entry = await setup_integration()

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert diagnostics["entry"]["data"]["token"] == REDACTED
    assert diagnostics["entry"]["data"]["username"] == REDACTED
    assert diagnostics["incident"]["id"] == 1000
    assert diagnostics["incident"]["body"] == REDACTED
    assert diagnostics["incident"]["address"] == REDACTED
    assert diagnostics["response"]["status"] == "acknowledged"
    assert diagnostics["response"]["user_name"] == REDACTED
