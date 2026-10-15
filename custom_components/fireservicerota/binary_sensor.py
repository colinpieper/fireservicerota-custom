"""Binary Sensor platform for FireServiceRota integration."""

from typing import Any, override

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

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
    """Set up FireServiceRota binary sensor based on a config entry."""

    coordinator = entry.runtime_data
    client = coordinator.client

    async_add_entities(
        [
            ResponseBinarySensor(coordinator, client, entry),
            IncidentActiveBinarySensor(client),
            IncidentAssignedBinarySensor(client),
        ]
    )


class ResponseBinarySensor(
    CoordinatorEntity[FireServiceUpdateCoordinator], BinarySensorEntity
):
    """Representation of an FireServiceRota sensor."""

    _attr_has_entity_name = True
    _attr_translation_key = "duty"

    def __init__(
        self,
        coordinator: FireServiceUpdateCoordinator,
        client: FireServiceRotaClient,
        entry: ConfigEntry,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._client = client
        self._attr_unique_id = f"{entry.unique_id}_Duty"

    @property
    @override
    def is_on(self) -> bool | None:
        """Return the state of the binary sensor."""
        return self._client.on_duty

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return available attributes for binary sensor."""
        attr: dict[str, Any] = {}
        if not self.coordinator.data:
            return attr

        data = self.coordinator.data
        return {
            key: data[key]
            for key in (
                "start_time",
                "end_time",
                "available",
                "active",
                "assigned_function_ids",
                "skill_ids",
                "type",
                "assigned_function",
            )
            if key in data
        }


class IncidentBinarySensor(BinarySensorEntity):
    """Base class for binary sensors about the last incident."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, client: FireServiceRotaClient) -> None:
        """Initialize."""
        self._client = client

    @override
    async def async_added_to_hass(self) -> None:
        """Register update callback."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_UPDATE.format(self._client.entry_id),
                self.async_write_ha_state,
            )
        )


class IncidentActiveBinarySensor(IncidentBinarySensor):
    """Binary sensor that is on while an incident is active."""

    _attr_translation_key = "incident_active"

    def __init__(self, client: FireServiceRotaClient) -> None:
        """Initialize."""
        super().__init__(client)
        self._attr_unique_id = f"{client.unique_id}_Active"

    @property
    @override
    def is_on(self) -> bool:
        """Return if an incident is active."""
        return self._client.active

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the active incident id and alerting state."""
        if not self._client.active or not (incident := self._client.incident):
            return {}
        return {"incident_id": incident.get("id"), "state": incident.get("state")}


class IncidentAssignedBinarySensor(IncidentBinarySensor):
    """Binary sensor that is on when the user is assigned to the incident."""

    _attr_translation_key = "incident_assigned"

    def __init__(self, client: FireServiceRotaClient) -> None:
        """Initialize."""
        super().__init__(client)
        self._attr_unique_id = f"{client.unique_id}_Assigned"

    @property
    @override
    def is_on(self) -> bool | None:
        """Return if the user is assigned to a seat."""
        if (response := self._client.response) is None:
            return None
        return bool(response.get("assigned_skill_ids"))

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return assignment details."""
        if (response := self._client.response) is None:
            return {}
        return {
            "incident_id": self._client.incident_id,
            "assigned_skill_ids": response.get("assigned_skill_ids") or [],
            "suggested_skill_ids": self._client.system_assigned_skill_ids or [],
            "status": response.get("status"),
            "reported_status": response.get("reported_status"),
        }
