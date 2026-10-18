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
    assert summarize_incident(assigned, USER_ID, now) == {
        "created_at": assigned["created_at"],
        "acknowledged": True,
        "assigned": True,
        "minutes": 42,
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
    assert not summary["acknowledged"]
    assert not summary["final"]
    assert summarize_incident(open_incident, USER_ID, created + timedelta(days=7))[
        "final"
    ]


async def test_history_statistics(
    recorder_mock: Any,
    hass: HomeAssistant,
    mock_api: MagicMock,
    setup_integration: Callable[[], Any],
) -> None:
    """Test the full history is imported as cumulative hourly statistics."""
    first = datetime(2026, 8, 3, 14, 12, tzinfo=dt_util.UTC)
    second = datetime(2026, 9, 20, 2, 40, tzinfo=dt_util.UTC)
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
    ]
    serve(mock_api, incidents, per_page=2)

    with patch("custom_components.fireservicerota.history.HISTORY_PER_PAGE", 2):
        await setup_integration()
        await hass.async_block_till_done(wait_background_tasks=True)

    first_hour = first.replace(minute=0)
    second_hour = second.replace(minute=0)
    assert await get_sums(hass, "incidents") == [(first_hour, 2), (second_hour, 3)]
    assert await get_sums(hass, "acknowledged") == [(first_hour, 1), (second_hour, 2)]
    assert await get_sums(hass, "assigned") == [(first_hour, 0), (second_hour, 1)]
    assert await get_sums(hass, "response_minutes") == [
        (first_hour, 0),
        (second_hour, 55),
    ]
    assert {call.args[0] for call in mock_api.get_incident.call_args_list} == {1, 2, 3}


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
    assert (await get_sums(hass, "response_minutes"))[-1][1] == 0

    open_incident["end_time"] = (created + timedelta(minutes=30)).isoformat()
    mock_api.get_incident.reset_mock()
    async_dispatcher_send(
        hass, SIGNAL_EVENT.format(config_entry.entry_id), EVENT_ENDED, {"id": 2}
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert [call.args[0] for call in mock_api.get_incident.call_args_list] == [2]
    assert (await get_sums(hass, "response_minutes"))[-1][1] == 30
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
