"""The FireServiceRota integration."""

from typing import Any

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, EVENT_ENDED, HISTORY_SYNC_INTERVAL, SIGNAL_EVENT
from .coordinator import (
    FireServiceConfigEntry,
    FireServiceRotaClient,
    FireServiceUpdateCoordinator,
)
from .history import IncidentHistory
from .services import async_setup_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
    Platform.EVENT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the FireServiceRota actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: FireServiceConfigEntry) -> bool:
    """Set up FireServiceRota from a config entry."""

    client = FireServiceRotaClient(hass, entry)
    await client.setup()

    if client.token_refresh_failure:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN,
            translation_key="token_refresh_failed",
        )

    entry.async_on_unload(client.async_shutdown)
    coordinator = FireServiceUpdateCoordinator(hass, client, entry)

    await coordinator.async_config_entry_first_refresh()

    coordinator.history = history = IncidentHistory(hass, client)
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Entities have restored their state, now load the latest incident.
    entry.async_create_task(hass, client.async_initialize(), "initialize incident")

    @callback
    def _async_sync_history(*_args: Any) -> None:
        entry.async_create_background_task(
            hass, history.async_sync(), "sync incident history"
        )

    @callback
    def _async_on_event(event_type: str, _data: dict[str, Any]) -> None:
        if event_type == EVENT_ENDED:
            _async_sync_history()

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, SIGNAL_EVENT.format(entry.entry_id), _async_on_event
        )
    )
    entry.async_on_unload(
        async_track_time_interval(hass, _async_sync_history, HISTORY_SYNC_INTERVAL)
    )
    _async_sync_history()

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: FireServiceConfigEntry
) -> bool:
    """Unload FireServiceRota config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
