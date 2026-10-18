"""Additional FireServiceRota API calls not provided by pyfireservicerota."""

from datetime import UTC, datetime, timedelta
from typing import Any

from pyfireservicerota import FireServiceRota

from .const import INCIDENT_TYPE_ALERT


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
