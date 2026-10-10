"""The FireServiceRota integration."""

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import DOMAIN
from .coordinator import (
    FireServiceConfigEntry,
    FireServiceRotaClient,
    FireServiceUpdateCoordinator,
)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.SWITCH]


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

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Entities have restored their state, now load the latest incident.
    entry.async_create_task(hass, client.async_initialize(), "initialize incident")

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: FireServiceConfigEntry
) -> bool:
    """Unload FireServiceRota config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
