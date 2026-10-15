"""Tests for tracking the active incident."""

from datetime import timedelta
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    mock_restore_cache,
)

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

from .conftest import make_incident, make_message

SENSOR = "sensor.incidents"
SWITCH = "switch.incident_response"
ACTIVE = "binary_sensor.incident_active"


async def _advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    """Move time forward and run scheduled callbacks."""
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_startup_fetches_active_incident(
    hass: HomeAssistant, mock_api: MagicMock, setup_integration
) -> None:
    """Test the latest incident and response are fetched at startup."""
    mock_api.get_latest_incident.return_value = make_incident(
        status="acknowledged", assigned_skill_ids=[165]
    )

    await setup_integration()

    sensor = hass.states.get(SENSOR)
    assert sensor.state == "P 2 BLB-04 Test incident 1000"
    assert sensor.attributes["id"] == 1000
    assert sensor.attributes["state"] == "alerting"
    assert sensor.attributes["formatted_address"] == "Velvin 13\n6065BJ Montfort\n"

    switch = hass.states.get(SWITCH)
    assert switch.state == STATE_ON
    assert switch.attributes["assigned_skill_ids"] == [165]


async def test_startup_without_incident(
    hass: HomeAssistant, mock_api: MagicMock, setup_integration
) -> None:
    """Test entities stay unknown when there is no incident."""
    await setup_integration()

    assert hass.states.get(SENSOR).state == STATE_UNKNOWN
    assert hass.states.get(SWITCH).state == STATE_UNKNOWN
    mock_api.get_incident.assert_not_called()


async def test_startup_keeps_finished_alerting_active(
    hass: HomeAssistant, mock_api: MagicMock, setup_integration
) -> None:
    """Test an incident whose alerting has finished is still active."""
    mock_api.get_latest_incident.return_value = make_incident(state="finished")

    await setup_integration()

    sensor = hass.states.get(SENSOR)
    assert sensor.state == "P 2 BLB-04 Test incident 1000"
    assert sensor.attributes["state"] == "finished"
    assert hass.states.get(ACTIVE).state == STATE_ON


async def test_startup_falls_back_to_restored_incident(
    hass: HomeAssistant, mock_api: MagicMock, setup_integration
) -> None:
    """Test the restored incident is fetched when listing incidents fails."""
    incident = make_incident(status="acknowledged")
    mock_restore_cache(
        hass,
        [
            State(
                SENSOR,
                incident["body"],
                {"id": 1000, "created_at": incident["created_at"]},
            )
        ],
    )
    mock_api.get_incident.return_value = incident

    await setup_integration()

    mock_api.get_incident.assert_called_with(1000)
    assert hass.states.get(SENSOR).state == incident["body"]
    assert hass.states.get(SWITCH).state == STATE_ON


async def test_startup_keeps_old_restored_incident(
    hass: HomeAssistant, mock_api: MagicMock, setup_integration
) -> None:
    """Test an old restored incident stays shown as the last, inactive incident."""
    mock_restore_cache(
        hass,
        [
            State(
                SENSOR,
                "Old incident",
                {
                    "id": 1000,
                    "created_at": (dt_util.utcnow() - timedelta(days=2)).isoformat(),
                },
            )
        ],
    )

    await setup_integration()

    assert hass.states.get(SENSOR).state == "Old incident"
    assert hass.states.get(ACTIVE).state == STATE_OFF
    assert mock_api.get_incident.call_count == 1


async def test_new_and_update_messages(
    hass: HomeAssistant, mock_api: MagicMock, setup_integration, send_message
) -> None:
    """Test new and update messages are told apart by the integration."""
    await setup_integration()
    incident = make_incident()
    mock_api.get_incident.return_value = incident

    await send_message(make_message(incident))
    sensor = hass.states.get(SENSOR)
    assert sensor.state == incident["body"]
    assert sensor.attributes["trigger"] == "new"

    # The library marks messages as new after a websocket restart.
    await send_message(make_message(incident, body="Updated body", trigger="new"))
    sensor = hass.states.get(SENSOR)
    assert sensor.state == "Updated body"
    assert sensor.attributes["trigger"] == "update"


async def test_message_for_known_incident_after_restart_is_update(
    hass: HomeAssistant, mock_api: MagicMock, setup_integration, send_message
) -> None:
    """Test a message for the incident loaded at startup is an update."""
    incident = make_incident()
    mock_api.get_latest_incident.return_value = incident
    await setup_integration()

    await send_message(make_message(incident))

    assert hass.states.get(SENSOR).attributes["trigger"] == "update"


async def test_message_for_older_incident_is_ignored(
    hass: HomeAssistant, mock_api: MagicMock, setup_integration, send_message
) -> None:
    """Test a late message for an older incident does not replace the active one."""
    incident = make_incident(1001)
    mock_api.get_latest_incident.return_value = incident
    await setup_integration()

    older = make_incident(1000, created_at=dt_util.utcnow() - timedelta(minutes=10))
    await send_message(make_message(older))

    assert hass.states.get(SENSOR).attributes["id"] == 1001


async def test_polling_window(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: MagicMock,
    setup_integration,
    send_message,
) -> None:
    """Test the incident is polled quickly at first, then slowly."""
    await setup_integration()
    incident = make_incident()
    mock_api.get_incident.return_value = incident
    await send_message(make_message(incident))
    assert mock_api.get_incident.call_count == 1

    await _advance(hass, freezer, timedelta(seconds=30))
    assert mock_api.get_incident.call_count == 2

    # Past the fast window, polls happen every five minutes.
    for _ in range(60):
        await _advance(hass, freezer, timedelta(seconds=30))
    calls = mock_api.get_incident.call_count
    await _advance(hass, freezer, timedelta(seconds=30))
    assert mock_api.get_incident.call_count == calls
    await _advance(hass, freezer, timedelta(minutes=5))
    assert mock_api.get_incident.call_count == calls + 1


async def test_late_assignment_is_picked_up(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: MagicMock,
    setup_integration,
    send_message,
) -> None:
    """Test an assignment made after the last websocket message reaches HA."""
    await setup_integration()
    incident = make_incident(status="acknowledged")
    mock_api.get_incident.return_value = incident
    await send_message(make_message(incident))
    assert hass.states.get(SWITCH).attributes["assigned_skill_ids"] == []

    mock_api.get_incident.return_value = make_incident(
        status="acknowledged", assigned_skill_ids=[165]
    )
    await _advance(hass, freezer, timedelta(seconds=30))

    assert hass.states.get(SWITCH).attributes["assigned_skill_ids"] == [165]


async def test_finished_alerting_does_not_end_incident(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: MagicMock,
    setup_integration,
    send_message,
) -> None:
    """Test the alerting state reaching finished keeps the incident active."""
    await setup_integration()
    incident = make_incident(status="acknowledged")
    mock_api.get_incident.return_value = make_incident(
        status="acknowledged", state="finished"
    )
    await send_message(make_message(incident))
    await _advance(hass, freezer, timedelta(seconds=30))

    assert hass.states.get(SENSOR).state == incident["body"]
    assert hass.states.get(ACTIVE).state == STATE_ON
    assert hass.states.get(SWITCH).state == STATE_ON

    calls = mock_api.get_incident.call_count
    await _advance(hass, freezer, timedelta(seconds=30))
    assert mock_api.get_incident.call_count == calls + 1


async def test_incident_ends_at_end_time(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: MagicMock,
    setup_integration,
    send_message,
) -> None:
    """Test an incident stops being active but stays shown once it has ended."""
    await setup_integration()
    incident = make_incident(status="acknowledged", assigned_skill_ids=[165])
    mock_api.get_incident.return_value = incident
    await send_message(make_message(incident))

    await _advance(hass, freezer, timedelta(hours=1))
    assert hass.states.get(ACTIVE).state == STATE_ON

    end_time = dt_util.utcnow()
    mock_api.get_incident.return_value = make_incident(
        status="acknowledged", assigned_skill_ids=[165], end_time=end_time
    )
    await _advance(hass, freezer, timedelta(minutes=5))
    assert hass.states.get(ACTIVE).state == STATE_OFF
    sensor = hass.states.get(SENSOR)
    assert sensor.state == incident["body"]
    assert sensor.attributes["id"] == 1000
    assert sensor.attributes["end_time"] == end_time.isoformat()
    assert hass.states.get(SWITCH).state == STATE_ON

    # Polling stops, and late messages do not make the incident active again.
    calls = mock_api.get_incident.call_count
    await _advance(hass, freezer, timedelta(minutes=10))
    assert mock_api.get_incident.call_count == calls
    await send_message(make_message(incident, trigger="update"))
    assert hass.states.get(ACTIVE).state == STATE_OFF

    # A new incident replaces it.
    new_incident = make_incident(1001)
    mock_api.get_incident.return_value = new_incident
    await send_message(make_message(new_incident))
    assert hass.states.get(ACTIVE).state == STATE_ON
    assert hass.states.get(SENSOR).attributes["id"] == 1001


async def test_future_end_time_keeps_incident_active(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: MagicMock,
    setup_integration,
    send_message,
) -> None:
    """Test an end time in the future does not end the incident yet."""
    await setup_integration()
    end_time = dt_util.utcnow() + timedelta(hours=1)
    incident = make_incident(end_time=end_time)
    mock_api.get_incident.return_value = incident
    await send_message(make_message(incident))

    await _advance(hass, freezer, timedelta(minutes=30))
    assert hass.states.get(ACTIVE).state == STATE_ON

    for _ in range(7):
        await _advance(hass, freezer, timedelta(minutes=5))
    assert hass.states.get(ACTIVE).state == STATE_OFF


async def test_startup_with_ended_incident(
    hass: HomeAssistant, mock_api: MagicMock, setup_integration
) -> None:
    """Test an incident that ended before startup is shown but not active."""
    mock_api.get_latest_incident.return_value = make_incident(
        created_at=dt_util.utcnow() - timedelta(hours=2),
        end_time=dt_util.utcnow() - timedelta(hours=1),
    )

    await setup_integration()

    assert hass.states.get(SENSOR).state == "P 2 BLB-04 Test incident 1000"
    assert hass.states.get(ACTIVE).state == STATE_OFF


async def test_incident_ends_after_maximum_age(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: MagicMock,
    setup_integration,
    send_message,
) -> None:
    """Test an incident that never gets an end time ends after 24 hours."""
    await setup_integration()
    incident = make_incident()
    mock_api.get_incident.return_value = incident
    await send_message(make_message(incident))

    await _advance(hass, freezer, timedelta(hours=23, minutes=50))
    assert hass.states.get(ACTIVE).state == STATE_ON

    for _ in range(4):
        await _advance(hass, freezer, timedelta(minutes=5))
    assert hass.states.get(ACTIVE).state == STATE_OFF
    assert hass.states.get(SENSOR).state == incident["body"]


async def test_unload_stops_polling(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: MagicMock,
    mock_websocket: MagicMock,
    setup_integration,
    send_message,
) -> None:
    """Test unloading stops the websocket and polling."""
    entry = await setup_integration()
    incident = make_incident()
    mock_api.get_incident.return_value = incident
    await send_message(make_message(incident))

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    mock_websocket.return_value.stop.assert_called_once()

    calls = mock_api.get_incident.call_count
    await _advance(hass, freezer, timedelta(minutes=10))
    assert mock_api.get_incident.call_count == calls
