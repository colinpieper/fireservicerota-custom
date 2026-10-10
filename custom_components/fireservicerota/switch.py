"""Switch platform for FireServiceRota integration."""

from typing import Any, override

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SIGNAL_UPDATE
from .coordinator import (
    FireServiceConfigEntry,
    FireServiceRotaClient,
    FireServiceUpdateCoordinator,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FireServiceConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up FireServiceRota switch based on a config entry."""
    coordinator = entry.runtime_data
    client = coordinator.client

    async_add_entities([ResponseSwitch(coordinator, client, entry)])


class ResponseSwitch(SwitchEntity):
    """Representation of an FireServiceRota switch."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "incident_response"

    def __init__(
        self,
        coordinator: FireServiceUpdateCoordinator,
        client: FireServiceRotaClient,
        entry: ConfigEntry,
    ) -> None:
        """Initialize."""
        self._coordinator = coordinator
        self._client = client
        self._attr_unique_id = f"{entry.unique_id}_Response"
        self._entry_id = entry.entry_id

    @property
    @override
    def icon(self) -> str:
        """Return the icon to use in the frontend."""
        status = (self._client.response or {}).get("status")
        if status == "acknowledged":
            return "mdi:run-fast"
        if status == "rejected":
            return "mdi:account-off-outline"

        return "mdi:forum"

    @property
    @override
    def is_on(self) -> bool | None:
        """Return if the incident was acknowledged."""
        if not (response := self._client.response) or "status" not in response:
            return None
        return response["status"] == "acknowledged"

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return available attributes for switch."""
        if not (data := self._client.response):
            return {}

        return {
            key: data[key]
            for key in (
                "user_name",
                "assigned_skill_ids",
                "responded_at",
                "start_time",
                "status",
                "reported_status",
                "arrived_at_station",
                "available_at_incident_creation",
                "active_duty_function_ids",
            )
            if key in data
        }

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Send Acknowledge response status."""
        await self.async_set_response(True)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Send Reject response status."""
        await self.async_set_response(False)

    async def async_set_response(self, value: bool) -> None:
        """Send response status."""
        await self._client.async_set_response(value)

    @override
    async def async_added_to_hass(self) -> None:
        """Register update callback."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_UPDATE.format(self._entry_id),
                self.client_update,
            )
        )

    @callback
    def client_update(self) -> None:
        """Handle updated incident data from the client."""
        self.async_write_ha_state()
