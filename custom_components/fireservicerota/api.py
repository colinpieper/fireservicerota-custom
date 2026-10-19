"""Additional FireServiceRota API calls not provided by pyfireservicerota."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pyfireservicerota import FireServiceRota

from .const import INCIDENT_TYPE_ALERT
from .schedule import availability, merge_intervals

# Days of the combined schedule fetched on every update, starting today.
SCHEDULE_DAYS = 8


class FireServiceRotaApi(FireServiceRota):
    """FireServiceRota API client with full incident access."""

    @property
    def user_id(self) -> int | None:
        """Return the id of the authenticated user, if known."""
        return self._user["id"] if self._user else None

    def get_incident(self, incident_id: int) -> dict[str, Any] | None:
        """Get an incident including all incident responses."""
        if not self._user:
            self.get_user()

        response = self._request(
            "GET",
            endpoint=f"incidents/{incident_id}",
            log_msg_action="get incident",
            auth_request=False,
        )
        return response if isinstance(response, dict) else None

    def get_schedule(
        self, start: date, end: date, tzstring: str
    ) -> list[dict[str, Any]] | None:
        """Get the user's combined schedule intervals from start to end date.

        The intervals of the day before are included, so a period that
        started then isn't cut off at the start date.
        """
        if not self._user:
            self.get_user()

        tz = ZoneInfo(tzstring)
        membership_id = self._user["memberships"][0]["id"]
        # The API requires both times to align with the start of a day.
        response = self._request(
            "GET",
            endpoint=f"memberships/{membership_id}/combined_schedule",
            log_msg_action="get schedule memberships",
            params={
                "start_time": datetime.combine(
                    start - timedelta(days=1), time(), tz
                ).isoformat(),
                "end_time": datetime.combine(end, time(), tz).isoformat(),
            },
            auth_request=False,
        )
        if not isinstance(response, dict):
            return None
        return response.get("intervals") or []

    def get_availability(self, tzstring: str) -> dict[str, Any]:
        """Get the current availability period and the upcoming schedule.

        Unlike the library, this also returns unavailable periods, the next
        period and the schedule periods from today.
        """
        today = datetime.now(ZoneInfo(tzstring)).date()
        schedule_end = today + timedelta(days=SCHEDULE_DAYS)
        intervals = self.get_schedule(today, schedule_end, tzstring)
        if intervals is None:
            return {"available": False}

        periods = merge_intervals(intervals)
        data = availability(periods, datetime.now(UTC))
        if data["available"] and data.get("assigned_function_ids"):
            for function_id in data["assigned_function_ids"]:
                data["assigned_function"] = self.get_standby_function(function_id)[
                    "name"
                ]
        data["schedule"] = periods
        data["schedule_start"] = today.isoformat()
        data["schedule_end"] = schedule_end.isoformat()
        return data

    def set_availability(
        self,
        start_time: datetime,
        end_time: datetime,
        available: bool | None,
        comments: str | None = None,
    ) -> dict[str, Any] | None:
        """Set the availability of the user's membership for a period.

        None clears the period, so the regular schedule applies again.
        """
        if not self._user:
            self.get_user()

        membership_id = self._user["memberships"][0]["id"]
        body: dict[str, Any] = {
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "available": available,
            "channel": "geofencing",
        }
        if comments:
            body["comments"] = comments

        response = self._request(
            "POST",
            endpoint=f"memberships/{membership_id}/schedule_exceptions",
            log_msg_action="set availability",
            body=body,
            auth_request=False,
        )
        return response if isinstance(response, dict) else None

    def get_incidents_page(
        self, page: int, per_page: int
    ) -> list[dict[str, Any]] | None:
        """Get one page of incidents without incident responses."""
        incidents = self._request(
            "GET",
            endpoint="incidents",
            log_msg_action="get incidents page",
            params={"page": page, "per_page": per_page},
            auth_request=False,
        )
        return incidents if isinstance(incidents, list) else None

    def get_latest_incident(self, max_age: timedelta) -> dict[str, Any] | None:
        """Get the most recent incident alert including all incident responses."""
        created_after = datetime.now(UTC) - max_age
        incidents = self._request(
            "GET",
            endpoint="incidents",
            log_msg_action="get recent incidents",
            params={"per_page": 25, "created_after": created_after.isoformat()},
            auth_request=False,
        )
        if not isinstance(incidents, list):
            return None

        alerts = [
            incident
            for incident in incidents
            if incident.get("type") == INCIDENT_TYPE_ALERT
            and incident.get("created_at")
        ]
        if not alerts:
            return None

        latest = max(
            alerts, key=lambda incident: datetime.fromisoformat(incident["created_at"])
        )
        return self.get_incident(latest["id"])
