"""The FireServiceRota integration."""

from collections import deque
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any, override

from pyfireservicerota import (
    ExpiredTokenError,
    FireServiceRota,
    FireServiceRotaIncidents,
    InvalidAuthError,
    InvalidTokenError,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN, CONF_URL, CONF_USERNAME
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api import FireServiceRotaApi
from .const import (
    DOMAIN,
    EVENT_ENDED,
    EVENT_NEW,
    EVENT_RESPONSE_CLOSED,
    EVENT_UPDATE,
    FAST_POLL_INTERVAL,
    FAST_POLL_WINDOW,
    INCIDENT_MAX_AGE,
    RESPONSE_CLOSED_ATTEMPTS,
    RESPONSE_CLOSED_DELAY,
    RESPONSE_CLOSED_RETRY_DELAY,
    SIGNAL_EVENT,
    SIGNAL_UPDATE,
    SLOW_POLL_INTERVAL,
    WSS_BWRURL,
)

if TYPE_CHECKING:
    from .history import IncidentHistory

MIN_TIME_BETWEEN_UPDATES = timedelta(seconds=60)

# Cooldown between incident fetches triggered by bursts of websocket messages.
REFRESH_COOLDOWN = 2

_LOGGER = logging.getLogger(__name__)

# Incident details that are only needed to find the user's own data.
DETAIL_ONLY_KEYS = {
    "incident_responses",
    "incident_skill_assignments",
    "warning_statuses",
    "groups",
    "characteristics",
    "notes",
    "trigger",
}

INCIDENT_EVENT_KEYS = (
    "id",
    "body",
    "prio",
    "type",
    "created_at",
    "can_respond_until",
    "task_ids",
    "message_to_speech_url",
)
ADDRESS_KEYS = ("latitude", "longitude", "address_type", "formatted_address")

type FireServiceConfigEntry = ConfigEntry[FireServiceUpdateCoordinator]


def _parse_datetime(value: Any) -> datetime | None:
    """Parse an API timestamp."""
    if not isinstance(value, str):
        return None
    return dt_util.parse_datetime(value)


def incident_event_data(incident: Mapping[str, Any]) -> dict[str, Any]:
    """Return the event data for a new or updated incident."""
    data = {key: incident[key] for key in INCIDENT_EVENT_KEYS if key in incident}
    if isinstance(address := incident.get("address"), Mapping):
        data.update({key: address[key] for key in ADDRESS_KEYS if key in address})
    return data


class FireServiceUpdateCoordinator(DataUpdateCoordinator[dict | None]):
    """Data update coordinator for FireServiceRota."""

    config_entry: FireServiceConfigEntry
    history: IncidentHistory

    def __init__(
        self,
        hass: HomeAssistant,
        client: FireServiceRotaClient,
        entry: FireServiceConfigEntry,
    ) -> None:
        """Initialize the FireServiceRota DataUpdateCoordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="duty binary sensor",
            config_entry=entry,
            update_interval=MIN_TIME_BETWEEN_UPDATES,
        )

        self.client = client

    @override
    async def _async_update_data(self) -> dict | None:
        """Get the latest availability data."""
        return await self.client.async_update()


class FireServiceRotaOauth:
    """Handle authentication tokens."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, fsr: FireServiceRota
    ) -> None:
        """Initialize the oauth object."""
        self._hass = hass
        self._entry = entry

        self._url = entry.data[CONF_URL]
        self._username = entry.data[CONF_USERNAME]
        self._fsr = fsr

    async def async_refresh_tokens(self) -> bool:
        """Refresh tokens and update config entry."""
        _LOGGER.debug("Refreshing authentication tokens after expiration")

        try:
            token_info = await self._hass.async_add_executor_job(
                self._fsr.refresh_tokens
            )

        except (InvalidAuthError, InvalidTokenError) as err:
            raise ConfigEntryAuthFailed(
                "Error refreshing tokens, triggered reauth workflow"
            ) from err

        _LOGGER.debug("Saving new tokens in config entry")
        self._hass.config_entries.async_update_entry(
            self._entry,
            data={
                "auth_implementation": DOMAIN,
                CONF_URL: self._url,
                CONF_USERNAME: self._username,
                CONF_TOKEN: token_info,
            },
        )

        return True


class FireServiceRotaWebSocket:
    """Define a FireServiceRota websocket manager object."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        on_incident: Callable[[dict[str, Any]], None],
    ) -> None:
        """Initialize the websocket object."""
        self._hass = hass
        self._entry = entry
        self._on_incident_callback = on_incident

        self._fsr_incidents = FireServiceRotaIncidents(on_incident=self._on_incident)

    def _construct_url(self) -> str:
        """Return URL with latest access token."""
        return WSS_BWRURL.format(
            self._entry.data[CONF_URL], self._entry.data[CONF_TOKEN]["access_token"]
        )

    def _on_incident(self, data: dict[str, Any]) -> None:
        """Received incident message in the websocket thread."""
        _LOGGER.debug("Received incident via websocket: %s", data)
        self._hass.loop.call_soon_threadsafe(self._on_incident_callback, data)

    def start_listener(self) -> None:
        """Start the websocket listener."""
        _LOGGER.debug("Starting incidents listener")
        self._fsr_incidents.start(self._construct_url())

    def stop_listener(self) -> None:
        """Stop the websocket listener."""
        _LOGGER.debug("Stopping incidents listener")
        self._fsr_incidents.stop()


class FireServiceRotaClient:
    """Getting the latest data from fireservicerota."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the data object."""
        self._hass = hass
        self._entry = entry

        self._url = entry.data[CONF_URL]
        self._tokens = entry.data[CONF_TOKEN]

        self.entry_id = entry.entry_id
        self.unique_id = entry.unique_id

        self.token_refresh_failure = False
        self.on_duty = False

        # The last incident and the user's own response to it.
        self.incident: dict[str, Any] | None = None
        self.active = False
        self.response: dict[str, Any] | None = None
        self.system_assigned_skill_ids: list[int] | None = None
        self.trigger: str | None = None
        self.response_deadline: datetime | None = None

        self._incident_seen_at: datetime | None = None
        self._ended_incident_ids: deque[int] = deque(maxlen=10)
        self._last_event_data: dict[str, Any] | None = None
        self._response_closed_sent = False
        self._response_closed_attempts = 0
        self._unsub_poll: CALLBACK_TYPE | None = None
        self._unsub_response_closed: CALLBACK_TYPE | None = None

        self.fsr = FireServiceRotaApi(base_url=self._url, token_info=self._tokens)

        self.oauth = FireServiceRotaOauth(
            self._hass,
            self._entry,
            self.fsr,
        )

        self.websocket = FireServiceRotaWebSocket(
            self._hass, self._entry, self._async_on_incident
        )

        self._refresh_debouncer = Debouncer(
            hass,
            _LOGGER,
            cooldown=REFRESH_COOLDOWN,
            immediate=True,
            function=self.async_refresh_incident,
        )

    @property
    def incident_id(self) -> int | None:
        """Return the id of the last incident."""
        return self.incident.get("id") if self.incident else None

    async def setup(self) -> None:
        """Set up the data client."""
        await self._hass.async_add_executor_job(self.websocket.start_listener)

    async def update_call(self, func, *args):
        """Perform update call and return data."""
        if self.token_refresh_failure:
            return None

        try:
            return await self._hass.async_add_executor_job(func, *args)
        except ExpiredTokenError, InvalidTokenError:
            await self._hass.async_add_executor_job(self.websocket.stop_listener)
            self.token_refresh_failure = True

            if await self.oauth.async_refresh_tokens():
                self.token_refresh_failure = False

                def _restart_and_call() -> Any:
                    self.websocket.start_listener()
                    return func(*args)

                return await self._hass.async_add_executor_job(_restart_and_call)

    async def async_update(self) -> dict | None:
        """Get the latest availability data."""
        data = await self.update_call(
            self.fsr.get_availability, str(self._hass.config.time_zone)
        )

        if not data:
            return None

        self.on_duty = bool(data.get("available"))

        _LOGGER.debug("Updated availability data: %s", data)
        return data

    @callback
    def async_restore_incident(self, body: str, attributes: Mapping[str, Any]) -> None:
        """Restore the last known incident until the API has been queried."""
        if self.incident is not None or "id" not in attributes:
            return

        incident: dict[str, Any] = {
            key: attributes[key]
            for key in (
                *INCIDENT_EVENT_KEYS,
                "responder_mode",
                "state",
                "start_time",
                "end_time",
            )
            if key in attributes
        }
        incident["body"] = body
        if address := {
            key: attributes[key] for key in ADDRESS_KEYS if key in attributes
        }:
            incident["address"] = address

        self.incident = incident
        self.trigger = attributes.get("trigger")
        self._incident_seen_at = dt_util.utcnow()
        self.active = not self._incident_over()
        _LOGGER.debug("Restored incident %s", self.incident_id)

    async def async_initialize(self) -> None:
        """Load the latest incident and response from the API after startup."""
        data = await self.update_call(self.fsr.get_latest_incident, INCIDENT_MAX_AGE)
        if data is None and self.incident_id is not None:
            data = await self.update_call(self.fsr.get_incident, self.incident_id)

        if data is not None and data.get("id") != self.incident_id:
            self.incident = None
            self.response = None
            self.system_assigned_skill_ids = None
            self.trigger = None
            self._incident_seen_at = dt_util.utcnow()

        if data is not None:
            self._apply_incident(data, initial=True)

        if self.incident is None:
            async_dispatcher_send(self._hass, SIGNAL_UPDATE.format(self.entry_id))
            return

        self.active = True
        if self._incident_over():
            self._async_end_incident(fire_event=False)
            return

        # Only announce the final response when its deadline is still ahead.
        if (
            self.response_deadline is not None
            and self.response_deadline + RESPONSE_CLOSED_DELAY > dt_util.utcnow()
        ):
            self._response_closed_sent = False
            self._async_schedule_response_closed()
        else:
            self._response_closed_sent = True

        self._async_schedule_poll()
        async_dispatcher_send(self._hass, SIGNAL_UPDATE.format(self.entry_id))

    async def async_refresh_incident(self) -> bool:
        """Fetch the last incident and the user's response to it."""
        if (incident_id := self.incident_id) is None:
            return False

        _LOGGER.debug("Updating incident and response data for id %s", incident_id)
        data = await self.update_call(self.fsr.get_incident, incident_id)
        if not data:
            return False

        self._apply_incident(data)
        return True

    async def async_set_response(self, value: bool) -> None:
        """Set incident response status."""
        if not self.incident_id:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="no_active_incident",
            )

        _LOGGER.debug(
            "Setting incident response for incident id '%s' to state '%s'",
            self.incident_id,
            value,
        )

        await self.update_call(self.fsr.set_incident_response, self.incident_id, value)
        await self.async_refresh_incident()

    async def async_set_availability(
        self,
        start_time: datetime,
        end_time: datetime,
        available: bool | None,
        comments: str | None = None,
    ) -> None:
        """Set the user's availability for a period."""
        _LOGGER.debug(
            "Setting availability from %s to %s to %s", start_time, end_time, available
        )

        if not await self.update_call(
            self.fsr.set_availability, start_time, end_time, available, comments
        ):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="set_availability_failed",
            )

    async def async_stop_listener(self) -> None:
        """Stop listener."""
        await self._hass.async_add_executor_job(self.websocket.stop_listener)

    async def async_shutdown(self) -> None:
        """Stop the websocket listener and all scheduled updates."""
        self._async_cancel_timers()
        self._refresh_debouncer.async_shutdown()
        await self.async_stop_listener()

    @callback
    def _async_on_incident(self, message: dict[str, Any]) -> None:
        """Handle an incident message from the websocket."""
        if (incident_id := message.get("id")) is None or "body" not in message:
            return

        if incident_id in self._ended_incident_ids:
            _LOGGER.debug("Ignoring message for ended incident %s", incident_id)
            return

        if incident_id == self.incident_id:
            trigger = EVENT_UPDATE
        else:
            current_created = _parse_datetime((self.incident or {}).get("created_at"))
            message_created = _parse_datetime(message.get("created_at"))
            if (
                current_created
                and message_created
                and message_created < current_created
            ):
                _LOGGER.debug("Ignoring message for older incident %s", incident_id)
                return

            trigger = EVENT_NEW
            self._async_cancel_timers()
            self.active = True
            self._last_event_data = None
            self.incident = {}
            self.response = None
            self.system_assigned_skill_ids = None
            self.response_deadline = None
            self._incident_seen_at = dt_util.utcnow()
            self._response_closed_sent = False
            self._response_closed_attempts = 0

        self.trigger = trigger
        self.incident = {
            **(self.incident or {}),
            **{key: value for key, value in message.items() if key != "trigger"},
        }
        self._async_update_deadline(message.get("can_respond_until"))

        if trigger == EVENT_NEW:
            self._async_schedule_poll()
        elif self._incident_over():
            self._async_end_incident(fire_event=True)
            return

        # Alerting sends bursts of messages that mostly repeat the same data.
        if (event_data := incident_event_data(self.incident)) != self._last_event_data:
            self._last_event_data = event_data
            async_dispatcher_send(
                self._hass, SIGNAL_EVENT.format(self.entry_id), trigger, event_data
            )
        async_dispatcher_send(self._hass, SIGNAL_UPDATE.format(self.entry_id))
        self._refresh_debouncer.async_schedule_call()

    @callback
    def _apply_incident(self, data: dict[str, Any], *, initial: bool = False) -> None:
        """Apply full incident data fetched from the API."""
        incident_id = data.get("id")
        if incident_id is None or incident_id != self.incident_id and not initial:
            return

        previous_state = (self.incident or {}).get("state")
        self.incident = {
            **(self.incident or {}),
            **{
                key: value for key, value in data.items() if key not in DETAIL_ONLY_KEYS
            },
        }

        if (user_id := self.fsr.user_id) is not None:
            self.response = next(
                (
                    response
                    for response in data.get("incident_responses") or []
                    if response.get("user_id") == user_id
                ),
                self.response,
            )
            if "incident_skill_assignments" in data:
                self.system_assigned_skill_ids = next(
                    (
                        assignment.get("skill_ids") or []
                        for assignment in data["incident_skill_assignments"] or []
                        if assignment.get("user_id") == user_id
                    ),
                    [],
                )

        self._async_update_deadline(data.get("can_respond_until"), schedule=not initial)

        # The state is about alerting the responders, not about the incident.
        if (state := data.get("state")) != previous_state:
            _LOGGER.debug("Incident %s alerting state is now %s", incident_id, state)

        if not initial and self._incident_over():
            self._async_end_incident(fire_event=True)
            return

        if not initial:
            async_dispatcher_send(self._hass, SIGNAL_UPDATE.format(self.entry_id))

    @callback
    def _async_update_deadline(self, value: Any, *, schedule: bool = True) -> None:
        """Track the earliest response deadline seen for the incident."""
        if (deadline := _parse_datetime(value)) is None:
            return
        if self.response_deadline is not None and deadline >= self.response_deadline:
            return

        self.response_deadline = deadline
        if schedule:
            self._async_schedule_response_closed()

    @callback
    def _async_schedule_response_closed(self) -> None:
        """Schedule fetching the final response after the response deadline."""
        if self._unsub_response_closed:
            self._unsub_response_closed()
            self._unsub_response_closed = None

        if self._response_closed_sent or self.response_deadline is None:
            return

        delay = self.response_deadline + RESPONSE_CLOSED_DELAY - dt_util.utcnow()
        self._unsub_response_closed = async_call_later(
            self._hass, max(delay, timedelta(0)), self._async_response_closed
        )

    async def _async_response_closed(self, _now: datetime) -> None:
        """Fetch the final response and announce it."""
        self._unsub_response_closed = None
        incident_id = self.incident_id
        if incident_id is None or self._response_closed_sent:
            return

        self._response_closed_attempts += 1
        refreshed = await self.async_refresh_incident()
        if self.incident_id != incident_id:
            return

        if not refreshed and self._response_closed_attempts < RESPONSE_CLOSED_ATTEMPTS:
            self._unsub_response_closed = async_call_later(
                self._hass, RESPONSE_CLOSED_RETRY_DELAY, self._async_response_closed
            )
            return

        self._response_closed_sent = True
        response = self.response or {}
        assigned_skill_ids = response.get("assigned_skill_ids") or []
        status = response.get("status")
        _LOGGER.debug(
            "Response closed for incident %s: status %s, assigned skills %s",
            incident_id,
            status,
            assigned_skill_ids,
        )
        async_dispatcher_send(
            self._hass,
            SIGNAL_EVENT.format(self.entry_id),
            EVENT_RESPONSE_CLOSED,
            {
                "id": incident_id,
                "body": (self.incident or {}).get("body"),
                "status": status,
                "acknowledged": status == "acknowledged",
                "assigned": bool(assigned_skill_ids),
                "assigned_skill_ids": assigned_skill_ids,
                "reported_status": response.get("reported_status"),
                "available_at_incident_creation": response.get(
                    "available_at_incident_creation"
                ),
                "on_duty": response.get("on_duty"),
            },
        )

    @callback
    def _async_schedule_poll(self) -> None:
        """Schedule the next incident poll, or end an incident that is over."""
        if self._unsub_poll:
            self._unsub_poll()
            self._unsub_poll = None

        if not self.active:
            return

        if self._incident_over():
            self._async_end_incident(fire_event=True)
            return

        age = self._incident_age()
        interval = FAST_POLL_INTERVAL if age < FAST_POLL_WINDOW else SLOW_POLL_INTERVAL
        self._unsub_poll = async_call_later(self._hass, interval, self._async_poll)

    async def _async_poll(self, _now: datetime) -> None:
        """Poll the active incident."""
        self._unsub_poll = None
        await self.async_refresh_incident()
        if self.active and self._unsub_poll is None:
            self._async_schedule_poll()

    def _incident_over(self) -> bool:
        """Return if the last incident has ended or is too old to be active."""
        end_time = _parse_datetime((self.incident or {}).get("end_time"))
        return (
            end_time is not None and end_time <= dt_util.utcnow()
        ) or self._incident_age() >= INCIDENT_MAX_AGE

    def _incident_age(self) -> timedelta:
        """Return the time since the last incident was created."""
        started = (
            _parse_datetime((self.incident or {}).get("created_at"))
            or self._incident_seen_at
            or dt_util.utcnow()
        )
        return dt_util.utcnow() - started

    @callback
    def _async_end_incident(self, *, fire_event: bool) -> None:
        """Mark the incident as no longer active, keeping it as the last one."""
        if (incident := self.incident) is None or not self.active:
            return

        _LOGGER.debug("Incident %s is no longer active", incident.get("id"))
        self._async_cancel_timers()
        self._ended_incident_ids.append(incident.get("id"))
        self.active = False
        self._response_closed_sent = True

        if fire_event:
            async_dispatcher_send(
                self._hass,
                SIGNAL_EVENT.format(self.entry_id),
                EVENT_ENDED,
                {
                    "id": incident.get("id"),
                    "body": incident.get("body"),
                    "state": incident.get("state"),
                    "end_time": incident.get("end_time"),
                },
            )
        async_dispatcher_send(self._hass, SIGNAL_UPDATE.format(self.entry_id))

    @callback
    def _async_cancel_timers(self) -> None:
        """Cancel scheduled polls and the response deadline."""
        if self._unsub_poll:
            self._unsub_poll()
            self._unsub_poll = None
        if self._unsub_response_closed:
            self._unsub_response_closed()
            self._unsub_response_closed = None
