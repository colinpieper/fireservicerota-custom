"""Sensor platform for FireServiceRota integration."""

import logging
from typing import Any, override

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import SIGNAL_HISTORY, SIGNAL_UPDATE
from .coordinator import ADDRESS_KEYS, FireServiceConfigEntry, FireServiceRotaClient
from .history import IncidentHistory, deployment_stats

_LOGGER = logging.getLogger(__name__)

INCIDENT_ATTRIBUTES = (
    "id",
    "created_at",
    "message_to_speech_url",
    "prio",
    "type",
    "responder_mode",
    "can_respond_until",
    "task_ids",
    "state",
    "start_time",
    "end_time",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FireServiceConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up FireServiceRota sensor based on a config entry."""
    async_add_entities(
        [
            IncidentsSensor(entry.runtime_data.client),
            DeploymentPercentageSensor(
                entry.runtime_data.client, entry.runtime_data.history
            ),
        ]
    )


# pylint: disable-next=home-assistant-invalid-inheritance # needs fixing
class IncidentsSensor(RestoreEntity, SensorEntity):
    """Representation of FireServiceRota incidents sensor."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "incidents"

    def __init__(self, client: FireServiceRotaClient) -> None:
        """Initialize."""
        self._client = client
        self._entry_id = self._client.entry_id
        self._attr_unique_id = f"{self._client.unique_id}_Incidents"

    @property
    @override
    def icon(self) -> str:
        """Return the icon to use in the frontend."""
        prio = (self._client.incident or {}).get("prio")
        if isinstance(prio, str) and prio.startswith("a"):
            return "mdi:ambulance"

        return "mdi:fire-truck"

    @property
    @override
    def native_value(self) -> str | None:
        """Return the body of the active incident."""
        return (self._client.incident or {}).get("body")

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return available attributes for sensor."""
        if not (data := self._client.incident):
            return {}

        attr: dict[str, Any] = {
            key: data[key] for key in INCIDENT_ATTRIBUTES if data.get(key)
        }
        if self._client.trigger:
            attr["trigger"] = self._client.trigger
        if isinstance(address := data.get("address"), dict):
            attr.update({key: address[key] for key in ADDRESS_KEYS if key in address})

        return attr

    @override
    async def async_added_to_hass(self) -> None:
        """Run when about to be added to hass."""
        await super().async_added_to_hass()

        if (state := await self.async_get_last_state()) and "id" in state.attributes:
            self._client.async_restore_incident(state.state, state.attributes)
            _LOGGER.debug("Restored entity 'Incidents' to: %s", state.state)

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_UPDATE.format(self._entry_id),
                self.client_update,
            )
        )

    @callback
    def client_update(self) -> None:
        """Handle updated data from the data client."""
        self.async_write_ha_state()


class DeploymentPercentageSensor(SensorEntity):
    """Deployment percentage as used for the fair deployment proposal."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "deployment_percentage"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, client: FireServiceRotaClient, history: IncidentHistory) -> None:
        """Initialize."""
        self._entry_id = client.entry_id
        self._history = history
        self._stats: dict[str, int] | None = None
        self._attr_unique_id = f"{client.unique_id}_DeploymentPercentage"

    @property
    @override
    def native_value(self) -> int | None:
        """Return the deployment percentage."""
        return self._stats["percentage"] if self._stats else None

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the counts the percentage is based on."""
        if not self._stats:
            return {}
        return {key: value for key, value in self._stats.items() if key != "percentage"}

    @override
    async def async_added_to_hass(self) -> None:
        """Run when about to be added to hass."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_HISTORY.format(self._entry_id),
                self.history_update,
            )
        )

    @callback
    def history_update(self) -> None:
        """Handle a synced incident history."""
        self._stats = deployment_stats(self._history.incidents, dt_util.now())
        self.async_write_ha_state()
