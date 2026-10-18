"""Tests for the FireServiceRota incident history statistics."""

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.fireservicerota.const import EVENT_ENDED, SIGNAL_EVENT
from custom_components.fireservicerota.history import (
    deployment_stats,
    statistic_id,
    summarize_incident,
)
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .conftest import USER_ID, make_incident

START = datetime(2026, 1, 1, tzinfo=dt_util.UTC)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    recorder_mock: Any, enable_custom_integrations: None
) -> None:
    """Set up the recorder before hass and enable the custom integration."""


@pytest.fixture(autouse=True)
def no_request_delay() -> None:
    """Do not wait between incident requests."""
    with (
        patch("custom_components.fireservicerota.history.HISTORY_REQUEST_DELAY", 0),
        patch("custom_components.fireservicerota.history.HISTORY_RETRY_DELAY", 0),
    ):
        yield


def list_item(incident: dict[str, Any]) -> dict[str, Any]:
    """Return an incident as listed by GET /incidents."""
    return {key: incident[key] for key in ("id", "type", "created_at")}


def serve(mock_api: MagicMock, incidents: list[dict[str, Any]], per_page: int = 100):
    """Serve the incidents from the mocked API in pages."""
    by_id = {incident["id"]: incident for incident in incidents}

    def _page(page: int, _per_page: int) -> list[dict[str, Any]]:
        start = (page - 1) * per_page
        return [list_item(incident) for incident in incidents[start : start + per_page]]

    mock_api.get_incidents_page.side_effect = _page
    mock_api.get_incident.side_effect = by_id.get


async def get_sums(hass: HomeAssistant, key: str) -> list[tuple[datetime, float]]:
    """Return the hourly sums of a statistic."""
    await async_wait_recording_done(hass)
    stat_id = statistic_id(USER_ID, key)
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        START,
        None,
        {stat_id},
        "hour",
        None,
        {"sum"},
    )
    return [
        (dt_util.utc_from_timestamp(row["start"]), row["sum"])
        for row in stats.get(stat_id, [])
    ]


def test_summarize_incident() -> None:
    """Test the user's part in an incident is summarized."""
    created = datetime(2026, 9, 1, 10, 0, tzinfo=dt_util.UTC)
    now = created + timedelta(days=1)

    assigned = make_incident(
        1,
        created_at=created,
        status="acknowledged",
        assigned_skill_ids=[165],
        end_time=created + timedelta(minutes=42, seconds=20),
    )
    summary = summarize_incident(assigned, USER_ID, now)
    assert summary.pop("responses") == {
        "7": {
            "name": None,
            "available": False,
            "group_available": False,
            "on_duty": False,
            "status": "acknowledged",
            "reported_status": None,
            "assigned": True,
        },
        str(USER_ID): {
            "name": "Colin",
            "available": False,
            "group_available": False,
            "on_duty": False,
            "status": "acknowledged",
            "reported_status": "dispatched",
            "assigned": True,
        },
    }
    assert summary == {
        "created_at": assigned["created_at"],
        "alerted": True,
        "acknowledged": True,
        "assigned": True,
        "minutes": 42,
        "available": False,
        "on_duty": False,
        "status": "acknowledged",
        "reported_status": "dispatched",
        "final": True,
    }

    rejected = make_incident(
        2, created_at=created, status="rejected", end_time=created + timedelta(hours=1)
    )
    summary = summarize_incident(rejected, USER_ID, now)
    assert not summary["acknowledged"]
    assert not summary["assigned"]
    assert summary["minutes"] == 0

    long = make_incident(
        3,
        created_at=created,
        status="acknowledged",
        assigned_skill_ids=[165],
        end_time=created + timedelta(days=3),
    )
    assert summarize_incident(long, USER_ID, now)["minutes"] == 1440

    open_incident = make_incident(4, created_at=created, status=None)
    summary = summarize_incident(open_incident, USER_ID, now)
    assert not summary["alerted"]
    assert not summary["acknowledged"]
    assert not summary["final"]
    assert summarize_incident(open_incident, USER_ID, created + timedelta(days=7))[
        "final"
    ]


def summary(
    created: datetime,
    *,
    status: str = "rejected",
    reported_status: str = "unknown",
    assigned: bool = False,
    available: bool = False,
    on_duty: bool = False,
    alerted: bool = True,
) -> dict[str, Any]:
    """Return a stored incident."""
    return {
        "created_at": created.isoformat(),
        "alerted": alerted,
        "acknowledged": status == "acknowledged",
        "assigned": assigned,
        "minutes": 0,
        "available": available,
        "on_duty": on_duty,
        "status": status,
        "reported_status": reported_status,
        "final": True,
    }


def test_deployment_stats() -> None:
    """Test the deployment percentage of the fair deployment proposal."""
    now = datetime(2026, 10, 1, 10, 0, tzinfo=dt_util.UTC)
    day = timedelta(days=1)
    incidents = [
        # Last year and not alerted, both left out.
        summary(datetime(2025, 12, 30, tzinfo=dt_util.UTC), assigned=True),
        summary(now - day, alerted=False, available=True),
        # Real deployments.
        summary(now - 2 * day, status="acknowledged", assigned=True),
        summary(now - 3 * day, status="acknowledged", reported_status="dispatched"),
        # Virtual: available or on duty, but rejected, ignored or no show.
        summary(now - 4 * day, available=True),
        summary(now - 5 * day, on_duty=True, status="expired"),
        summary(
            now - 6 * day,
            available=True,
            status="acknowledged",
            reported_status="no_show",
        ),
        # Not deployments: not available, or came to the station only.
        summary(now - 7 * day),
        summary(
            now - 8 * day,
            available=True,
            status="acknowledged",
            reported_status="shown_up",
        ),
    ]
    assert deployment_stats(incidents, now) == {
        "percentage": 71,
        "alarms": 7,
        "real_deployments": 2,
        "virtual_deployments": 3,
    }

    # At most the last 10 deployments count.
    deployed = [
        summary(now - i * day, status="acknowledged", assigned=True)
        for i in range(1, 13)
    ]
    assert deployment_stats([*deployed, summary(now - 20 * day)], now) == {
        "percentage": 100,
        "alarms": 10,
        "real_deployments": 10,
        "virtual_deployments": 0,
    }

    assert deployment_stats([], now) is None


async def test_history_statistics(
    recorder_mock: Any,
    hass: HomeAssistant,
    mock_api: MagicMock,
    setup_integration: Callable[[], Any],
) -> None:
    """Test the full history is imported as cumulative hourly statistics."""
    first = datetime(2026, 8, 3, 14, 12, tzinfo=dt_util.UTC)
    second = datetime(2026, 9, 20, 2, 40, tzinfo=dt_util.UTC)
    before = datetime(2026, 2, 10, 8, 5, tzinfo=dt_util.UTC)
    incidents = [
        make_incident(
            3,
            created_at=second,
            status="acknowledged",
            assigned_skill_ids=[165],
            end_time=second + timedelta(minutes=55),
        ),
        {**make_incident(99, created_at=second), "type": "incident_report"},
        make_incident(
            2, created_at=first, status="rejected", end_time=first + timedelta(hours=1)
        ),
        make_incident(
            1,
            created_at=first + timedelta(minutes=30),
            status="acknowledged",
            end_time=first + timedelta(hours=1),
        ),
        # Before the user joined, without a response of the user.
        make_incident(
            4, created_at=before, status=None, end_time=before + timedelta(hours=1)
        ),
    ]
    incidents[2]["incident_responses"][1]["available_at_incident_creation"] = True
    # The API returns fewer incidents per page than asked for.
    serve(mock_api, incidents, per_page=2)

    await setup_integration()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert mock_api.get_incidents_page.call_count == 4
    before_hour = before.replace(minute=0)
    first_hour = first.replace(minute=0)
    second_hour = second.replace(minute=0)
    assert await get_sums(hass, "incidents") == [
        (before_hour, 0),
        (first_hour, 2),
        (second_hour, 3),
    ]
    assert await get_sums(hass, "acknowledged") == [
        (before_hour, 0),
        (first_hour, 1),
        (second_hour, 2),
    ]
    assert await get_sums(hass, "assigned") == [
        (before_hour, 0),
        (first_hour, 0),
        (second_hour, 1),
    ]
    assert await get_sums(hass, "response_time") == [
        (before_hour, 0),
        (first_hour, 0),
        (second_hour, 0.92),
    ]

    # Available for incident 2 on a weekday afternoon, but rejected it.
    state = hass.states.get("sensor.deployment_percentage_day")
    assert state.state == "50"
    assert state.attributes["alarms"] == 2
    assert state.attributes["real_deployments"] == 0
    assert state.attributes["virtual_deployments"] == 1
    # Assigned to incident 3 on a Sunday night.
    state = hass.states.get("sensor.deployment_percentage_evening_night_weekend")
    assert state.state == "100"
    assert state.attributes["alarms"] == 1
    assert state.attributes["real_deployments"] == 1
    assert {call.args[0] for call in mock_api.get_incident.call_args_list} == {
        1,
        2,
        3,
        4,
    }


async def test_history_incremental(
    recorder_mock: Any,
    hass: HomeAssistant,
    mock_api: MagicMock,
    setup_integration: Callable[[], Any],
) -> None:
    """Test only new and unfinished incidents are fetched again."""
    created = dt_util.utcnow() - timedelta(hours=2)
    finished = make_incident(
        1,
        created_at=created - timedelta(days=1),
        status="acknowledged",
        end_time=created,
    )
    open_incident = make_incident(
        2, created_at=created, status="acknowledged", assigned_skill_ids=[165]
    )
    serve(mock_api, [open_incident, finished])

    config_entry = await setup_integration()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert mock_api.get_incident.call_count == 2
    assert (await get_sums(hass, "response_time"))[-1][1] == 0

    open_incident["end_time"] = (created + timedelta(minutes=30)).isoformat()
    mock_api.get_incident.reset_mock()
    async_dispatcher_send(
        hass, SIGNAL_EVENT.format(config_entry.entry_id), EVENT_ENDED, {"id": 2}
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert [call.args[0] for call in mock_api.get_incident.call_args_list] == [2]
    assert (await get_sums(hass, "response_time"))[-1][1] == 0.5
    assert (await get_sums(hass, "incidents"))[-1][1] == 2


async def test_history_paging_ignored(
    recorder_mock: Any,
    hass: HomeAssistant,
    mock_api: MagicMock,
    setup_integration: Callable[[], Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test the sync stops when the API returns the same page again."""
    created = datetime(2026, 9, 1, 10, 0, tzinfo=dt_util.UTC)
    incidents = [
        make_incident(i, created_at=created + timedelta(hours=i), status="rejected")
        for i in range(1, 3)
    ]
    mock_api.get_incidents_page.side_effect = None
    mock_api.get_incidents_page.return_value = [list_item(i) for i in incidents]
    mock_api.get_incident.side_effect = {i["id"]: i for i in incidents}.get

    with patch("custom_components.fireservicerota.history.HISTORY_PER_PAGE", 2):
        await setup_integration()
        await hass.async_block_till_done(wait_background_tasks=True)

    assert mock_api.get_incidents_page.call_count == 2
    assert "history is limited to 2 incidents" in caplog.text
    assert (await get_sums(hass, "incidents"))[-1][1] == 2


async def test_history_rate_limited(
    hass: HomeAssistant,
    mock_api: MagicMock,
    setup_integration: Callable[[], Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test a failed request is retried once and stops the sync until the next."""
    created = datetime(2026, 9, 1, 10, 0, tzinfo=dt_util.UTC)
    incidents = [
        make_incident(i, created_at=created - timedelta(days=i), status="rejected")
        for i in range(1, 5)
    ]
    serve(mock_api, incidents)
    by_id = {incident["id"]: incident for incident in incidents}
    responses = {1: [None], 2: [], 3: [None, None]}

    def _get_incident(incident_id: int) -> dict[str, Any] | None:
        if pending := responses.get(incident_id):
            return pending.pop(0)
        return by_id[incident_id]

    mock_api.get_incident.side_effect = _get_incident

    config_entry = await setup_integration()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert [call.args[0] for call in mock_api.get_incident.call_args_list] == [
        1,
        1,
        2,
        3,
        3,
    ]
    assert "Could not fetch incident 3" in caplog.text
    assert (await get_sums(hass, "incidents"))[-1][1] == 2

    mock_api.get_incident.reset_mock()
    async_dispatcher_send(
        hass, SIGNAL_EVENT.format(config_entry.entry_id), EVENT_ENDED, {"id": 1}
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert [call.args[0] for call in mock_api.get_incident.call_args_list] == [3, 4]
    assert (await get_sums(hass, "incidents"))[-1][1] == 4


async def test_history_fetch_failed(
    recorder_mock: Any,
    hass: HomeAssistant,
    mock_api: MagicMock,
    setup_integration: Callable[[], Any],
) -> None:
    """Test no statistics are imported when the incidents can't be listed."""
    mock_api.get_incidents_page.return_value = None

    await setup_integration()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert await get_sums(hass, "incidents") == []
