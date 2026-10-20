"""Long-term statistics of the user's incident history."""

import asyncio
from collections import Counter, defaultdict
from collections.abc import AsyncIterator, Collection, Iterable, Mapping
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
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import DurationConverter

from .const import (
    DAY_SCHEDULE_END,
    DAY_SCHEDULE_START,
    DEPLOYMENT_WINDOW,
    DOMAIN,
    HISTORY_FINAL_AGE,
    HISTORY_PER_PAGE,
    HISTORY_REQUEST_DELAY,
    HISTORY_RETRY_DELAY,
    HISTORY_UPDATED_MARGIN,
    INCIDENT_TYPE_ALERT,
    MAX_RESPONSE_MINUTES,
    SCHEDULE_DAY,
    SCHEDULE_EVENING_NIGHT_WEEKEND,
    SIGNAL_HISTORY,
)
from .coordinator import FireServiceRotaClient

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

# Statistic key: (name, unit, unit class).
STATISTICS: dict[str, tuple[str, str | None, str | None]] = {
    "incidents": ("FireServiceRota incidents", None, None),
    "acknowledged": ("FireServiceRota acknowledged incidents", None, None),
    "assigned": ("FireServiceRota assigned incidents", None, None),
    "response_time": (
        "FireServiceRota response time",
        UnitOfTime.HOURS,
        DurationConverter.UNIT_CLASS,
    ),
}

# Stored incidents without all these keys are fetched again.
INCIDENT_KEYS = {
    "created_at",
    "alerted",
    "acknowledged",
    "assigned",
    "minutes",
    "available",
    "on_duty",
    "status",
    "reported_status",
    "responses",
    "approval_status",
    "final",
}

# Response statuses that are not declining or ignoring the alert.
NOT_DECLINED_STATUSES = {"acknowledged", "not_sent"}

# Order of the response statuses when ranking candidates for a seat.
STATUS_RANKS = {"acknowledged": 0, "pending": 1, "not_sent": 1}

# Priority groups of the fair deployment proposal, highest first.
PRIORITY_GROUPS = ("on_duty", "paraat", "available", "unavailable")


def statistic_id(user_id: int, key: str) -> str:
    """Return the statistic id of a user's incident statistic."""
    return f"{DOMAIN}:user_{user_id}_{key}"


def incident_schedule(created_at: datetime) -> str:
    """Return the duty schedule an incident falls in, by its local start time."""
    local = dt_util.as_local(created_at)
    if local.weekday() < 5 and DAY_SCHEDULE_START <= local.hour < DAY_SCHEDULE_END:
        return SCHEDULE_DAY
    return SCHEDULE_EVENING_NIGHT_WEEKEND


def summarize_response(response: Mapping[str, Any]) -> dict[str, Any]:
    """Return a user's response to an incident as stored in the history."""
    return {
        "name": response.get("user_name"),
        "available": bool(response.get("available_at_incident_creation")),
        "group_available": bool(response.get("group_available_at_incident_creation")),
        "on_duty": bool(response.get("on_duty")),
        "status": response.get("status"),
        "reported_status": response.get("reported_status"),
        "assigned": bool(response.get("assigned_skill_ids")),
    }


def priority_group(response: Mapping[str, Any]) -> str:
    """Return the priority group of a stored response at the alarm."""
    if response["on_duty"]:
        return "on_duty"
    if response["available"]:
        return "paraat" if response["group_available"] else "available"
    return "unavailable"


def is_real_deployment(incident: Mapping[str, Any]) -> bool:
    """Return if the user went out on the incident.

    Users that went out without being assigned a seat, e.g. added afterwards
    by editing the incident, are reported as dispatched.
    """
    return incident["assigned"] or incident["reported_status"] == "dispatched"


def is_virtual_deployment(incident: Mapping[str, Any]) -> bool:
    """Return if the user should have come by availability, but didn't."""
    if not (incident["available"] or incident["on_duty"]):
        return False
    return (
        incident["status"] not in NOT_DECLINED_STATUSES
        or incident["reported_status"] == "no_show"
    )


def deployment_stats(
    incidents: Iterable[Mapping[str, Any]],
    now: datetime,
    user_id: int | None = None,
    schedule: str | None = None,
) -> dict[str, int] | None:
    """Return the deployment percentage as used for the fair deployment proposal.

    (real + virtual deployments) / alarms, over the current calendar year or
    since the last DEPLOYMENT_WINDOW deployments, whichever is shorter. Without
    a user id it is the user's own, with a schedule only incidents in it count.
    """
    year_start = dt_util.start_of_local_day(now).replace(month=1, day=1)
    alarms: list[tuple[datetime, Mapping[str, Any]]] = []
    for incident in incidents:
        created = dt_util.parse_datetime(incident["created_at"])
        if created < year_start or (
            schedule is not None and incident_schedule(created) != schedule
        ):
            continue
        if user_id is None:
            response = incident if incident["alerted"] else None
        else:
            response = incident["responses"].get(str(user_id))
        if response is not None:
            alarms.append((created, response))
    alarms.sort(key=lambda alarm: alarm[0], reverse=True)

    count = real = virtual = 0
    for _created, incident in alarms:
        if real + virtual >= DEPLOYMENT_WINDOW:
            break
        count += 1
        if is_real_deployment(incident):
            real += 1
        elif is_virtual_deployment(incident):
            virtual += 1

    if not count:
        return None
    return {
        "percentage": round(100 * (real + virtual) / count),
        "alarms": count,
        "real_deployments": real,
        "virtual_deployments": virtual,
    }


def rank_candidates(
    responses: Iterable[Mapping[str, Any]],
    user_ids: Collection[int],
    percentages: Mapping[int, int | None],
    suggested: Collection[int] = (),
) -> list[dict[str, Any]]:
    """Rank users' responses to an incident for a seat, best candidate first.

    Like the fair deployment proposal: users who acknowledged before users who
    can still respond, then by priority group, then by lowest deployment
    percentage. Users who declined are left out. Each candidate tells if the
    system suggested them for a seat and if they were assigned one.
    """
    candidates = []
    for response in responses:
        user_id = response.get("user_id")
        if user_id not in user_ids or response.get("status") not in STATUS_RANKS:
            continue
        candidates.append(
            {
                "user_id": user_id,
                "name": response.get("user_name"),
                "status": response["status"],
                "group": priority_group(summarize_response(response)),
                "percentage": percentages.get(user_id),
                "suggested": user_id in suggested,
                "assigned": bool(response.get("assigned_skill_ids")),
            }
        )
    candidates.sort(
        key=lambda candidate: (
            STATUS_RANKS[candidate["status"]],
            PRIORITY_GROUPS.index(candidate["group"]),
            101 if candidate["percentage"] is None else candidate["percentage"],
        )
    )
    return candidates


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
    if (
        assigned or response.get("reported_status") == "dispatched"
    ) and end_time is not None:
        minutes = round((end_time - created).total_seconds() / 60)
        minutes = min(max(minutes, 0), MAX_RESPONSE_MINUTES)

    return {
        "created_at": incident["created_at"],
        "alerted": alerted,
        "acknowledged": response.get("status") == "acknowledged",
        "assigned": assigned,
        "minutes": minutes,
        "available": bool(response.get("available_at_incident_creation")),
        "on_duty": bool(response.get("on_duty")),
        "status": response.get("status"),
        "reported_status": response.get("reported_status"),
        "responses": {
            str(response["user_id"]): summarize_response(response)
            for response in incident.get("incident_responses") or []
            if response.get("user_id") is not None
        },
        "approval_status": incident.get("approval_status"),
        # Incidents are edited after they ended, e.g. who went out.
        "final": incident.get("approval_status") == "approved"
        or now - created >= HISTORY_FINAL_AGE,
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
        self._synced_at: datetime | None = None
        self._lock = asyncio.Lock()

    async def async_sync(self, *_args: Any) -> None:
        """Fetch new incidents and update the statistics."""
        async with self._lock:
            if self._incidents is None:
                stored = await self._store.async_load() or {}
                self._incidents = stored.get("incidents", {})
                self._synced_at = dt_util.parse_datetime(stored.get("synced_at") or "")

            fsr = self._client.fsr
            if fsr.user_id is None:
                await self._client.update_call(fsr.get_user)
            if (user_id := fsr.user_id) is None:
                _LOGGER.debug("Unknown user, not syncing the incident history")
                return

            if await self._async_fetch(user_id):
                await self._store.async_save(
                    {
                        "incidents": self._incidents,
                        "synced_at": self._synced_at and self._synced_at.isoformat(),
                    }
                )
            self._async_import(user_id)
            async_dispatcher_send(
                self._hass, SIGNAL_HISTORY.format(self._client.entry_id)
            )

    @property
    def incidents(self) -> list[dict[str, Any]]:
        """Return the stored incidents that have all details."""
        return [
            incident
            for incident in (self._incidents or {}).values()
            if incident.keys() >= INCIDENT_KEYS
        ]

    @property
    def known_users(self) -> dict[int, str]:
        """Return the users that responded to stored incidents, by latest name."""
        users: dict[int, str] = {}
        for incident in sorted(
            self.incidents,
            key=lambda incident: dt_util.parse_datetime(incident["created_at"]),
        ):
            for user_id, response in incident["responses"].items():
                if response["name"]:
                    users[int(user_id)] = response["name"]
        return users

    async def _async_list(
        self, updated_after: datetime | None = None
    ) -> AsyncIterator[list[dict[str, Any]] | None]:
        """Yield the pages of listed incidents, None if a page failed."""
        fsr = self._client.fsr
        seen: set[int] = set()
        page = 1

        while True:
            incidents = await self._client.update_call(
                fsr.get_incidents_page, page, HISTORY_PER_PAGE, updated_after
            )
            if incidents is None:
                _LOGGER.warning("Could not fetch page %s of the incident history", page)
                yield None
                return
            if not incidents:
                return
            new = [incident for incident in incidents if incident.get("id") not in seen]
            if not new:
                _LOGGER.warning(
                    "Page %s of the incident history repeats earlier incidents, "
                    "the history is limited to %s incidents",
                    page,
                    len(seen),
                )
                return
            seen.update(incident["id"] for incident in new)
            yield new

            # The API may return fewer incidents per page than asked for, so
            # only an empty page ends the history.
            page += 1
            await asyncio.sleep(HISTORY_REQUEST_DELAY)

    async def _async_fetch(self, user_id: int) -> bool:
        """Fetch incidents that are new, not final or updated.

        Return if the history needs saving: any changed or the sync completed.
        """
        assert self._incidents is not None
        fsr = self._client.fsr
        started = dt_util.utcnow()
        complete = True
        changed = 0

        # Final incidents are only fetched again when edited since the last sync.
        updated: set[int] = set()
        if self._synced_at is not None:
            async for incidents in self._async_list(
                self._synced_at - HISTORY_UPDATED_MARGIN
            ):
                if incidents is None:
                    complete = False
                    break
                updated.update(incident["id"] for incident in incidents)
            await asyncio.sleep(HISTORY_REQUEST_DELAY)

        listed = 0
        stopped = False
        async for new in self._async_list():
            if new is None:
                complete = False
                break
            listed += len(new)

            for incident in new:
                if incident.get("type") != INCIDENT_TYPE_ALERT:
                    continue
                key = str(incident["id"])
                # Incidents stored by an older version are fetched again.
                if (
                    (stored := self._incidents.get(key))
                    and stored["final"]
                    and stored.keys() >= INCIDENT_KEYS
                    and incident["id"] not in updated
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
            if stopped:
                complete = False
                break

        if complete:
            self._synced_at = started
        _LOGGER.debug(
            "Synced incident history: %s incidents listed, %s updated, %s changed, "
            "%s stored",
            listed,
            len(updated),
            changed,
            len(self._incidents),
        )
        return changed > 0 or complete

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
            hour["assigned"] += is_real_deployment(incident)
            hour["response_time"] += incident["minutes"]

        for key, (name, unit, unit_class) in STATISTICS.items():
            total = 0
            statistics: list[StatisticData] = []
            for start in sorted(hours):
                total += hours[start][key]
                value = round(total / 60, 2) if unit == UnitOfTime.HOURS else total
                statistics.append(StatisticData(start=start, state=value, sum=value))

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
