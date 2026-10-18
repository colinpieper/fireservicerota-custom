"""Tests for the FireServiceRota actions."""

from datetime import datetime
from unittest.mock import MagicMock

from pyfireservicerota import ExpiredTokenError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fireservicerota.const import DOMAIN
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

SERVICE = "set_availability"


@pytest.fixture(autouse=True)
async def time_zone(hass: HomeAssistant) -> None:
    """Use a time zone with an offset from UTC."""
    await hass.config.async_set_time_zone("Europe/Amsterdam")


@pytest.mark.parametrize(
    ("availability", "available"),
    [("unavailable", False), ("available", True), ("clear", None)],
)
async def test_set_availability(
    hass: HomeAssistant,
    mock_api: MagicMock,
    setup_integration,
    availability: str,
    available: bool | None,
) -> None:
    """Test the availability is set for the period in local time."""
    await setup_integration()
    mock_api.set_availability.return_value = {"available": available}

    await hass.services.async_call(
        DOMAIN,
        SERVICE,
        {
            "start_time": "2026-10-01 14:00:00",
            "end_time": "2026-10-01 14:15:00",
            "availability": availability,
        },
        blocking=True,
    )

    start, end, value, comments = mock_api.set_availability.call_args.args
    assert start.isoformat() == "2026-10-01T14:00:00+02:00"
    assert end.isoformat() == "2026-10-01T14:15:00+02:00"
    assert value is available
    assert comments is None


async def test_set_availability_defaults_to_now(
    hass: HomeAssistant, mock_api: MagicMock, setup_integration
) -> None:
    """Test the period starts now and the duty sensor is refreshed."""
    await setup_integration()
    assert hass.states.get("binary_sensor.duty").state == STATE_ON
    mock_api.set_availability.return_value = {"available": False}
    mock_api.get_availability.return_value = {"available": False}

    await hass.services.async_call(
        DOMAIN,
        SERVICE,
        {
            "end_time": "2026-10-01 12:15:00",
            "availability": "unavailable",
            "comments": "Away",
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    start, _end, _value, comments = mock_api.set_availability.call_args.args
    # NOW is 10:00 UTC.
    assert start == datetime.fromisoformat("2026-10-01T12:00:00+02:00")
    assert comments == "Away"
    assert hass.states.get("binary_sensor.duty").state == STATE_OFF


async def test_set_availability_end_before_start(
    hass: HomeAssistant, mock_api: MagicMock, setup_integration
) -> None:
    """Test a period that ends before it starts is refused."""
    await setup_integration()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            {"end_time": "2026-10-01 11:00:00", "availability": "unavailable"},
            blocking=True,
        )
    mock_api.set_availability.assert_not_called()


async def test_set_availability_failed(
    hass: HomeAssistant, mock_api: MagicMock, setup_integration
) -> None:
    """Test a failed request raises an error."""
    await setup_integration()
    mock_api.set_availability.return_value = None

    with pytest.raises(HomeAssistantError, match="Failed to set the availability"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            {"end_time": "2026-10-01 12:15:00", "availability": "unavailable"},
            blocking=True,
        )


async def test_set_availability_refreshes_tokens(
    hass: HomeAssistant, mock_api: MagicMock, setup_integration
) -> None:
    """Test the request is retried after refreshing expired tokens."""
    await setup_integration()
    mock_api.refresh_tokens.return_value = {"access_token": "new"}
    mock_api.set_availability.side_effect = [
        ExpiredTokenError("expired"),
        {"available": False},
    ]

    await hass.services.async_call(
        DOMAIN,
        SERVICE,
        {"end_time": "2026-10-01 12:15:00", "availability": "unavailable"},
        blocking=True,
    )

    assert mock_api.set_availability.call_count == 2
    mock_api.refresh_tokens.assert_called_once()


async def test_set_availability_config_entry(
    hass: HomeAssistant,
    mock_api: MagicMock,
    setup_integration,
    config_entry: MockConfigEntry,
) -> None:
    """Test the action needs a loaded config entry."""
    await setup_integration()
    mock_api.set_availability.return_value = {"available": False}

    await hass.services.async_call(
        DOMAIN,
        SERVICE,
        {
            "config_entry_id": config_entry.entry_id,
            "end_time": "2026-10-01 12:15:00",
            "availability": "unavailable",
        },
        blocking=True,
    )
    mock_api.set_availability.assert_called_once()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            {
                "config_entry_id": "unknown",
                "end_time": "2026-10-01 12:15:00",
                "availability": "unavailable",
            },
            blocking=True,
        )

    await hass.config_entries.async_unload(config_entry.entry_id)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            {"end_time": "2026-10-01 12:15:00", "availability": "unavailable"},
            blocking=True,
        )
