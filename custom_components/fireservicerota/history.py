"""Long-term statistics of the user's incident history."""

import asyncio
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import datetime
import logging
from typing import Any

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import DurationConverter

from .const import (
    DOMAIN,
    HISTORY_FINAL_AGE,
    HISTORY_PER_PAGE,
    HISTORY_REQUEST_DELAY,
    HISTORY_RETRY_DELAY,
    INCIDENT_TYPE_ALERT,
    MAX_RESPONSE_MINUTES,
)
from .coordinator import FireServiceRotaClient

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

# Statistic key: (name, unit, unit class).
STATISTICS: dict[str, tuple[str, str | None, str | None]] = {
    "incidents": ("FireServiceRota incidents", None, None),
    "acknowledged": ("FireServiceRota acknowledged incidents", None, None),
    "assigned": ("FireServiceRota assigned incidents", None, None),
    "response_minutes": (
        "FireServiceRota response time",
        UnitOfTime.MINUTES,
        DurationConverter.UNIT_CLASS,
    ),
}


def statistic_id(user_id: int, key: str) -> str:
    """Return the statistic id of a user's incident statistic."""
    return f"{DOMAIN}:user_{user_id}_{key}"


def summarize_incident(
    incident: Mapping[str, Any], user_id: int, now: datetime
) -> dict[str, Any] | None:
    """Return the user's part in an incident as stored in the history."""
    if (created := dt_util.parse_datetime(incident.get("created_at") or "")) is None:
        return None

    response = next(
        (
            response
            for response in incident.get("incident_responses") or []
            if response.get("user_id") == user_id
        ),
        None,
    )
    # Users without a response were not alerted, e.g. before they joined.
    alerted = response is not None
    response = response or {}
    assigned = bool(response.get("assigned_skill_ids"))
    end_time = dt_util.parse_datetime(incident.get("end_time") or "")

    minutes = 0
    if assigned and end_time is not None:
        minutes = round((end_time - created).total_seconds() / 60)
        minutes = min(max(minutes, 0), MAX_RESPONSE_MINUTES)

    return {
        "created_at": incident["created_at"],
        "alerted": alerted,
        "acknowledged": response.get("status") == "acknowledged",
        "assigned": assigned,
        "minutes": minutes,
        # Without an end time the response can still change.
        "final": end_time is not None or now - created >= HISTORY_FINAL_AGE,
    }


class IncidentHistory:
    """Keep long-term statistics of all incident alerts of the user."""

    def __init__(self, hass: HomeAssistant, client: FireServiceRotaClient) -> None:
        """Initialize the incident history."""
        self._hass = hass
        self._client = client
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.history.{client.entry_id}"
        )
        self._incidents: dict[str, dict[str, Any]] | None = None
        self._lock = asyncio.Lock()

    async def async_sync(self, *_args: Any) -> None:
        """Fetch new incidents and update the statistics."""
        async with self._lock:
            if self._incidents is None:
                stored = await self._store.async_load() or {}
                self._incidents = stored.get("incidents", {})

            fsr = self._client.fsr
            if fsr.user_id is None:
                await self._client.update_call(fsr.get_user)
            if (user_id := fsr.user_id) is None:
                _LOGGER.debug("Unknown user, not syncing the incident history")
                return

            if await self._async_fetch(user_id):
                await self._store.async_save({"incidents": self._incidents})
            self._async_import(user_id)

    async def _async_fetch(self, user_id: int) -> bool:
        """Fetch incidents that are new or not final, return if any changed."""
        assert self._incidents is not None
        fsr = self._client.fsr
        seen: set[int] = set()
        changed = 0
        page = 1
        stopped = False

        while not stopped:
            incidents = await self._client.update_call(
                fsr.get_incidents_page, page, HISTORY_PER_PAGE
            )
            if incidents is None:
                _LOGGER.warning("Could not fetch page %s of the incident history", page)
                break
            if not incidents:
                break
            new = [incident for incident in incidents if incident.get("id") not in seen]
            if not new:
                _LOGGER.warning(
                    "Page %s of the incident history repeats earlier incidents, "
                    "the history is limited to %s incidents",
                    page,
                    len(seen),
                )
                break
            seen.update(incident["id"] for incident in new)

            for incident in new:
                if incident.get("type") != INCIDENT_TYPE_ALERT:
                    continue
                key = str(incident["id"])
                # Incidents stored before "alerted" existed are fetched again.
                if (
                    (stored := self._incidents.get(key))
                    and stored["final"]
                    and "alerted" in stored
                ):
                    continue

                details = await self._client.update_call(
                    fsr.get_incident, incident["id"]
                )
                if details is None:
                    # Most likely rate limited, try once more after a pause.
                    await asyncio.sleep(HISTORY_RETRY_DELAY)
                    details = await self._client.update_call(
                        fsr.get_incident, incident["id"]
                    )
                if details is None:
                    _LOGGER.warning(
                        "Could not fetch incident %s, continuing the incident "
                        "history on the next sync",
                        incident["id"],
                    )
                    stopped = True
                    break
                summary = summarize_incident(details, user_id, dt_util.utcnow())
                if summary and summary != stored:
                    self._incidents[key] = summary
                    changed += 1
                await asyncio.sleep(HISTORY_REQUEST_DELAY)

            # The API may return fewer incidents per page than asked for, so
            # only an empty page ends the history.
            page += 1
            await asyncio.sleep(HISTORY_REQUEST_DELAY)

        _LOGGER.debug(
            "Synced incident history: %s incidents listed, %s changed, %s stored",
            len(seen),
            changed,
            len(self._incidents),
        )
        return changed > 0

    def _async_import(self, user_id: int) -> None:
        """Import the cumulative hourly statistics of all stored incidents."""
        assert self._incidents is not None
        if "recorder" not in self._hass.config.components:
            return

        hours: defaultdict[datetime, Counter[str]] = defaultdict(Counter)
        for incident in self._incidents.values():
            created = dt_util.as_utc(dt_util.parse_datetime(incident["created_at"]))
            # Keep a row for every incident, so rows imported before can't
            # keep a sum that is no longer valid.
            hour = hours[created.replace(minute=0, second=0, microsecond=0)]
            if not incident.get("alerted", True):
                continue
            hour["incidents"] += 1
            hour["acknowledged"] += incident["acknowledged"]
            hour["assigned"] += incident["assigned"]
            hour["response_minutes"] += incident["minutes"]

        for key, (name, unit, unit_class) in STATISTICS.items():
            total = 0
            statistics: list[StatisticData] = []
            for start in sorted(hours):
                total += hours[start][key]
                statistics.append(StatisticData(start=start, state=total, sum=total))

            async_add_external_statistics(
                self._hass,
                StatisticMetaData(
                    mean_type=StatisticMeanType.NONE,
                    has_sum=True,
                    name=name,
                    source=DOMAIN,
                    statistic_id=statistic_id(user_id, key),
                    unit_class=unit_class,
                    unit_of_measurement=unit,
                ),
                statistics,
            )
