"""Event platform for FireServiceRota integration."""

from typing import Any, override

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    EVENT_ENDED,
    EVENT_NEW,
    EVENT_RESPONSE_CLOSED,
    EVENT_UPDATE,
    SIGNAL_EVENT,
)
from .coordinator import FireServiceConfigEntry, FireServiceRotaClient


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FireServiceConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up FireServiceRota event based on a config entry."""
    async_add_entities([IncidentEvent(entry.runtime_data.client)])


class IncidentEvent(EventEntity):
    """Event entity firing on incident changes."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "incident"
    _attr_event_types = [EVENT_NEW, EVENT_UPDATE, EVENT_RESPONSE_CLOSED, EVENT_ENDED]

    def __init__(self, client: FireServiceRotaClient) -> None:
        """Initialize."""
        self._client = client
        self._attr_unique_id = f"{client.unique_id}_Incident"

    @override
    async def async_added_to_hass(self) -> None:
        """Register event callback."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_EVENT.format(self._client.entry_id),
                self._async_handle_event,
            )
        )

    @callback
    def _async_handle_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Fire an incident event."""
        self._trigger_event(event_type, data)
        self.async_write_ha_state()
