"""Calendar platform for FireServiceRota integration."""

from datetime import date, datetime, timedelta
from typing import Any, override

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .coordinator import FireServiceConfigEntry, FireServiceUpdateCoordinator
from .schedule import available_stretches, parse_time

EVENT_SUMMARY = "Available"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FireServiceConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up FireServiceRota calendar based on a config entry."""
    async_add_entities([AvailabilityCalendar(entry.runtime_data, entry)])


class AvailabilityCalendar(
    CoordinatorEntity[FireServiceUpdateCoordinator], CalendarEntity
):
    """Calendar with an event for each period the user is available."""

    _attr_has_entity_name = True
    _attr_translation_key = "availability"

    def __init__(
        self, coordinator: FireServiceUpdateCoordinator, entry: FireServiceConfigEntry
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id}_Availability"

    @property
    @override
    def event(self) -> CalendarEvent | None:
        """Return the current or next available period."""
        data = self.coordinator.data or {}
        now = dt_util.now()
        return next(
            (event for event in _events(data.get("schedule", [])) if event.end > now),
            None,
        )

    @override
    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return the available periods between start and end date.

        Periods outside the schedule fetched on every update are requested
        from the API.
        """
        data = self.coordinator.data or {}
        if _covers(data, start_date, end_date):
            periods = data["schedule"]
        else:
            periods = await self.coordinator.client.async_get_schedule(
                dt_util.as_local(start_date).date(),
                dt_util.as_local(end_date).date() + timedelta(days=1),
            )
        return [
            event
            for event in _events(periods)
            if event.end > start_date and event.start < end_date
        ]


def _covers(data: dict[str, Any], start_date: datetime, end_date: datetime) -> bool:
    """Return if the fetched schedule covers the period."""
    if "schedule" not in data:
        return False
    schedule_start = dt_util.start_of_local_day(
        date.fromisoformat(data["schedule_start"])
    )
    schedule_end = dt_util.start_of_local_day(date.fromisoformat(data["schedule_end"]))
    return schedule_start <= start_date and end_date <= schedule_end


def _events(periods: list[dict[str, Any]]) -> list[CalendarEvent]:
    """Return a calendar event for each stretch of available periods."""
    return [
        CalendarEvent(
            start=parse_time(stretch["start_time"]),
            end=parse_time(stretch["end_time"]),
            summary=EVENT_SUMMARY,
            description="\n".join([", ".join(stretch["types"]), *stretch["comments"]]),
            uid=stretch["start_time"],
        )
        for stretch in available_stretches(periods)
    ]
