"""Sensor platform for FireServiceRota integration."""

from collections.abc import Mapping
import logging
from typing import Any, override

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import CONF_COLLEAGUES, SCHEDULES, SIGNAL_HISTORY, SIGNAL_UPDATE
from .coordinator import ADDRESS_KEYS, FireServiceConfigEntry, FireServiceRotaClient
from .history import (
    IncidentHistory,
    deployment_stats,
    incident_schedule,
    rank_candidates,
)

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
    client = entry.runtime_data.client
    history = entry.runtime_data.history
    colleagues = {
        int(user_id): name
        for user_id, name in entry.options.get(CONF_COLLEAGUES, {}).items()
    }

    entities: list[SensorEntity] = [IncidentsSensor(client)]
    for schedule in SCHEDULES:
        entities.append(DeploymentPercentageSensor(client, history, schedule))
        entities.extend(
            DeploymentPercentageSensor(client, history, schedule, user_id, name)
            for user_id, name in colleagues.items()
        )
    if colleagues:
        entities.append(AspirantRankingSensor(client, history, colleagues))
    async_add_entities(entities)


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
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        client: FireServiceRotaClient,
        history: IncidentHistory,
        schedule: str,
        user_id: int | None = None,
        name: str | None = None,
    ) -> None:
        """Initialize the percentage of the user, or of a colleague by user id."""
        self._entry_id = client.entry_id
        self._history = history
        self._schedule = schedule
        self._user_id = user_id
        self._stats: dict[str, int] | None = None
        if user_id is None:
            self._attr_translation_key = f"deployment_percentage_{schedule}"
        else:
            self._attr_translation_key = f"colleague_deployment_percentage_{schedule}"
            self._attr_translation_placeholders = {"name": name or str(user_id)}
        self._attr_unique_id = (
            f"{client.unique_id}_DeploymentPercentage_{user_id or 'self'}_{schedule}"
        )

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
        self._stats = deployment_stats(
            self._history.incidents, dt_util.now(), self._user_id, self._schedule
        )
        self.async_write_ha_state()


class AspirantRankingSensor(SensorEntity):
    """The user's position for a seat among colleagues during an incident."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "aspirant_ranking"

    def __init__(
        self,
        client: FireServiceRotaClient,
        history: IncidentHistory,
        colleagues: Mapping[int, str],
    ) -> None:
        """Initialize."""
        self._client = client
        self._history = history
        self._colleagues = colleagues
        self._candidates: list[dict[str, Any]] = []
        self._schedule: str | None = None
        self._attr_unique_id = f"{client.unique_id}_AspirantRanking"

    @property
    @override
    def native_value(self) -> int | None:
        """Return the user's position among the users who acknowledged."""
        acknowledged = [
            candidate["user_id"]
            for candidate in self._candidates
            if candidate["status"] == "acknowledged"
        ]
        if (user_id := self._client.fsr.user_id) not in acknowledged:
            return None
        return acknowledged.index(user_id) + 1

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the ranked candidates for the seat."""
        if not self._candidates:
            return {}
        return {
            "incident_id": self._client.incident_id,
            "schedule": self._schedule,
            "candidates": [
                {key: value for key, value in candidate.items() if key != "user_id"}
                for candidate in self._candidates
            ],
        }

    @override
    async def async_added_to_hass(self) -> None:
        """Run when about to be added to hass."""
        for signal in (SIGNAL_UPDATE, SIGNAL_HISTORY):
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    signal.format(self._client.entry_id),
                    self.async_update_ranking,
                )
            )

    @callback
    def async_update_ranking(self) -> None:
        """Rank the user and colleagues for the active incident."""
        self._candidates = []
        self._schedule = None
        user_id = self._client.fsr.user_id
        created = dt_util.parse_datetime(
            (self._client.incident or {}).get("created_at") or ""
        )
        if self._client.active and user_id is not None and created is not None:
            self._schedule = incident_schedule(created)
            user_ids = {user_id, *self._colleagues}
            now = dt_util.now()
            # Like the proposal at the alarm, without the incident itself.
            incidents = [
                incident
                for incident in self._history.incidents
                if dt_util.parse_datetime(incident["created_at"]) < created
            ]
            percentages = {
                uid: (
                    stats["percentage"]
                    if (
                        stats := deployment_stats(
                            incidents,
                            now,
                            None if uid == user_id else uid,
                            self._schedule,
                        )
                    )
                    else None
                )
                for uid in user_ids
            }
            suggested = {
                assignment.get("user_id")
                for assignment in self._client.skill_assignments
                if assignment.get("skill_ids")
            }
            self._candidates = rank_candidates(
                self._client.responses, user_ids, percentages, suggested
            )
        self.async_write_ha_state()
