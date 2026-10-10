"""Tests for the FireServiceRota API additions."""

from datetime import timedelta
from unittest.mock import patch

from custom_components.fireservicerota.api import FireServiceRotaApi

from .conftest import USER_ID


def test_get_latest_incident() -> None:
    """Test the latest incident alert is selected and fetched in full."""
    api = FireServiceRotaApi(base_url="www.brandweerrooster.nl", token_info={})
    api._user = {"id": USER_ID}
    incidents = [
        {"id": 1, "type": "incident_alert", "created_at": "2026-10-01T10:00:00+02:00"},
        {"id": 3, "type": "activity_report", "created_at": "2026-10-01T12:00:00+02:00"},
        {"id": 2, "type": "incident_alert", "created_at": "2026-10-01T11:00:00+02:00"},
    ]

    def _request(method, endpoint, log_msg_action, params=None, **kwargs):
        if endpoint == "incidents":
            assert "created_after" in params
            return incidents
        return {"id": int(endpoint.split("/")[1]), "incident_responses": []}

    with patch.object(api, "_request", side_effect=_request):
        assert api.get_latest_incident(timedelta(hours=24))["id"] == 2
        assert api.user_id == USER_ID


def test_get_latest_incident_none() -> None:
    """Test no incident is returned when the request fails or is empty."""
    api = FireServiceRotaApi(base_url="www.brandweerrooster.nl", token_info={})
    api._user = {"id": USER_ID}

    with patch.object(api, "_request", return_value=None):
        assert api.get_latest_incident(timedelta(hours=24)) is None
    with patch.object(api, "_request", return_value=[]):
        assert api.get_latest_incident(timedelta(hours=24)) is None
