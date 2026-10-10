"""Additional FireServiceRota API calls not provided by pyfireservicerota."""

from datetime import datetime
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

    def get_latest_incident(self) -> dict[str, Any] | None:
        """Get the most recent incident alert including all incident responses."""
        incidents = self.get_incidents(limit=5)
        if not incidents:
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
