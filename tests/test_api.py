"""Tests for the FireServiceRota API additions."""

from datetime import date, datetime, timedelta
from unittest.mock import patch

from custom_components.fireservicerota.api import FireServiceRotaApi

from .conftest import SCHEDULE, USER_ID

TZ = "Europe/Amsterdam"


def test_get_schedule() -> None:
    """Test the schedule is requested from the start of the day before."""
    api = FireServiceRotaApi(base_url="www.brandweerrooster.nl", token_info={})
    user = {"id": USER_ID, "memberships": [{"id": 77}]}

    with (
        patch.object(api, "get_user", side_effect=lambda: setattr(api, "_user", user)),
        patch.object(api, "_request", return_value={"intervals": SCHEDULE}) as request,
    ):
        # Daylight saving time ends on 25 October.
        assert api.get_schedule(date(2026, 10, 25), date(2026, 10, 26), TZ) == SCHEDULE

    assert request.call_args.kwargs["endpoint"] == "memberships/77/combined_schedule"
    assert request.call_args.kwargs["params"] == {
        "start_time": "2026-10-24T00:00:00+02:00",
        "end_time": "2026-10-26T00:00:00+01:00",
    }

    with patch.object(api, "_request", return_value=None):
        assert api.get_schedule(date(2026, 10, 24), date(2026, 10, 26), TZ) is None


def test_get_availability() -> None:
    """Test the availability has the current period and the schedule."""
    api = FireServiceRotaApi(base_url="www.brandweerrooster.nl", token_info={})

    with patch.object(api, "get_schedule", return_value=SCHEDULE) as get_schedule:
        data = api.get_availability(TZ)

    get_schedule.assert_called_once_with(date(2026, 10, 1), date(2026, 10, 9), TZ)
    assert data["available"] is True
    assert data["start_time"] == "2026-10-01T08:00:00.000+02:00"
    assert data["next_start_time"] == "2026-10-01T14:00:00.000+02:00"
    assert len(data["schedule"]) == 5
    assert (data["schedule_start"], data["schedule_end"]) == (
        "2026-10-01",
        "2026-10-09",
    )

    with patch.object(api, "get_schedule", return_value=None):
        assert api.get_availability(TZ) == {"available": False}


def test_get_availability_assigned_function() -> None:
    """Test the name of an assigned standby duty function is added."""
    api = FireServiceRotaApi(base_url="www.brandweerrooster.nl", token_info={})
    schedule = [{**interval, "assigned_function_ids": [3]} for interval in SCHEDULE]

    with (
        patch.object(api, "get_schedule", return_value=schedule),
        patch.object(
            api, "get_standby_function", return_value={"name": "Chauffeur"}
        ) as get_function,
    ):
        assert api.get_availability(TZ)["assigned_function"] == "Chauffeur"

    get_function.assert_called_once_with(3)


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


def test_set_availability() -> None:
    """Test a schedule exception is created for the user's membership."""
    api = FireServiceRotaApi(base_url="www.brandweerrooster.nl", token_info={})
    user = {"id": USER_ID, "memberships": [{"id": 77}]}
    start = datetime.fromisoformat("2026-10-01T14:00:00+02:00")
    end = datetime.fromisoformat("2026-10-01T14:15:00+02:00")

    with (
        patch.object(api, "get_user", side_effect=lambda: setattr(api, "_user", user)),
        patch.object(api, "_request", return_value={"available": False}) as request,
    ):
        assert api.set_availability(start, end, False) == {"available": False}

    assert request.call_args.args == ("POST",)
    assert request.call_args.kwargs["endpoint"] == "memberships/77/schedule_exceptions"
    assert request.call_args.kwargs["body"] == {
        "start_time": "2026-10-01T14:00:00+02:00",
        "end_time": "2026-10-01T14:15:00+02:00",
        "available": False,
        "channel": "geofencing",
    }

    with patch.object(api, "_request", return_value=None) as request:
        assert api.set_availability(start, end, None, "Away") is None
    assert request.call_args.kwargs["body"]["available"] is None
    assert request.call_args.kwargs["body"]["comments"] == "Away"
