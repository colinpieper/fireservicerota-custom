"""Actions for the FireServiceRota integration."""

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_AVAILABILITY,
    ATTR_COMMENTS,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_END_TIME,
    ATTR_START_TIME,
    AVAILABILITY_AVAILABLE,
    AVAILABILITY_CLEAR,
    AVAILABILITY_UNAVAILABLE,
    DOMAIN,
    SERVICE_SET_AVAILABILITY,
)
from .coordinator import FireServiceConfigEntry

AVAILABILITY_VALUES = {
    AVAILABILITY_AVAILABLE: True,
    AVAILABILITY_UNAVAILABLE: False,
    AVAILABILITY_CLEAR: None,
}

SET_AVAILABILITY_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_START_TIME): cv.datetime,
        vol.Required(ATTR_END_TIME): cv.datetime,
        vol.Required(ATTR_AVAILABILITY): vol.In(AVAILABILITY_VALUES),
        vol.Optional(ATTR_COMMENTS): cv.string,
    }
)


def _get_entry(hass: HomeAssistant, entry_id: str | None) -> FireServiceConfigEntry:
    """Return the loaded config entry the action applies to."""
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
        and (entry_id is None or entry.entry_id == entry_id)
    ]
    if len(entries) != 1:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="config_entry_not_found"
            if entry_id or not entries
            else "config_entry_required",
        )
    return entries[0]


async def _async_set_availability(call: ServiceCall) -> None:
    """Set the user's availability for a period."""
    entry = _get_entry(call.hass, call.data.get(ATTR_CONFIG_ENTRY_ID))

    # Times without a time zone are local times.
    start_time = dt_util.as_local(call.data.get(ATTR_START_TIME) or dt_util.now())
    end_time = dt_util.as_local(call.data[ATTR_END_TIME])
    if end_time <= start_time:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="end_before_start",
        )

    coordinator = entry.runtime_data
    await coordinator.client.async_set_availability(
        start_time,
        end_time,
        AVAILABILITY_VALUES[call.data[ATTR_AVAILABILITY]],
        call.data.get(ATTR_COMMENTS),
    )
    await coordinator.async_request_refresh()


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the FireServiceRota actions."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_AVAILABILITY,
        _async_set_availability,
        schema=SET_AVAILABILITY_SCHEMA,
    )
