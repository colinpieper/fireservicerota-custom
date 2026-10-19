"""Tests for the FireServiceRota availability calendar."""

from datetime import date
from unittest.mock import MagicMock

import pytest

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

CALENDAR = "calendar.availability"


@pytest.fixture(autouse=True)
async def time_zone(hass: HomeAssistant) -> None:
    """Use the time zone of the schedule."""
    await hass.config.async_set_time_zone("Europe/Amsterdam")


async def get_events(hass: HomeAssistant, start: str, end: str) -> list[dict]:
    """Return the calendar events between two local times."""
    response = await hass.services.async_call(
        "calendar",
        "get_events",
        {"start_date_time": start, "end_date_time": end},
        target={"entity_id": CALENDAR},
        blocking=True,
        return_response=True,
    )
    return response[CALENDAR]["events"]


async def test_current_event(hass: HomeAssistant, setup_integration) -> None:
    """Test the calendar shows the current available period."""
    await setup_integration()

    state = hass.states.get(CALENDAR)
    assert state.state == STATE_ON
    assert state.attributes["message"] == "Available"
    assert state.attributes["start_time"] == "2026-10-01 08:00:00"
    assert state.attributes["end_time"] == "2026-10-01 14:00:00"
    assert state.attributes["description"] == "exception\nThuis"


async def test_events_from_schedule(
    hass: HomeAssistant, mock_api: MagicMock, setup_integration
) -> None:
    """Test events within the fetched schedule don't need a request."""
    await setup_integration()

    events = await get_events(hass, "2026-10-01 18:45:00", "2026-10-01 19:00:00")

    assert events == [
        {
            "start": "2026-10-01T18:45:00+02:00",
            "end": "2026-10-02T06:00:00+02:00",
            "summary": "Available",
            "description": "recurring",
        }
    ]
    assert await get_events(hass, "2026-10-01 14:00:00", "2026-10-01 18:45:00") == []
    mock_api.get_schedule.assert_not_called()


async def test_events_requested(
    hass: HomeAssistant, mock_api: MagicMock, setup_integration
) -> None:
    """Test events outside the fetched schedule are requested."""
    await setup_integration()

    events = await get_events(hass, "2026-09-30 00:00:00", "2026-10-01 10:00:00")

    assert [event["start"] for event in events] == ["2026-10-01T08:00:00+02:00"]
    mock_api.get_schedule.assert_called_once_with(
        date(2026, 9, 30), date(2026, 10, 2), "Europe/Amsterdam"
    )

    mock_api.get_schedule.return_value = None
    with pytest.raises(HomeAssistantError, match="availability schedule"):
        await get_events(hass, "2026-09-30 00:00:00", "2026-10-01 10:00:00")


async def test_no_schedule(
    hass: HomeAssistant, mock_api: MagicMock, setup_integration
) -> None:
    """Test the calendar is off without a schedule."""
    mock_api.get_availability.return_value = {"available": False}
    await setup_integration()

    assert hass.states.get(CALENDAR).state == STATE_OFF
