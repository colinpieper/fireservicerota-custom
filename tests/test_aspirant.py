"""Tests for colleagues' deployment percentages and the aspirant ranking."""

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from custom_components.fireservicerota.const import (
    CONF_COLLEAGUES,
    SCHEDULE_DAY,
    SCHEDULE_EVENING_NIGHT_WEEKEND,
)
from custom_components.fireservicerota.history import (
    deployment_stats,
    incident_schedule,
    rank_candidates,
    summarize_incident,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util

from .conftest import USER_ID, make_incident

MARK = 7
NICK = 8


@pytest.fixture(autouse=True)
def no_request_delay() -> None:
    """Do not wait between incident requests."""
    with (
        patch("custom_components.fireservicerota.history.HISTORY_REQUEST_DELAY", 0),
        patch("custom_components.fireservicerota.history.HISTORY_RETRY_DELAY", 0),
    ):
        yield


def local(*args: int) -> datetime:
    """Return a datetime in the local time zone."""
    return datetime(*args, tzinfo=dt_util.get_default_time_zone())


def response(user_id: int, name: str, status: str, **fields: Any) -> dict[str, Any]:
    """Return an incident response."""
    return {
        "user_id": user_id,
        "user_name": name,
        "status": status,
        "reported_status": "unknown",
        "assigned_skill_ids": [],
        "available_at_incident_creation": False,
        "group_available_at_incident_creation": False,
        "on_duty": False,
        **fields,
    }


def incident_with(
    incident_id: int, created: datetime, responses: list[dict[str, Any]]
) -> dict[str, Any]:
    """Return an incident with the given responses."""
    incident = make_incident(
        incident_id, created_at=created, end_time=created + timedelta(hours=1)
    )
    incident["incident_responses"] = responses
    return incident


def test_incident_schedule() -> None:
    """Test incidents fall in the day schedule on weekdays from 6 until 18."""
    # 2026-10-05 is a Monday, 2026-10-10 a Saturday.
    assert (
        incident_schedule(local(2026, 10, 5, 5, 59)) == SCHEDULE_EVENING_NIGHT_WEEKEND
    )
    assert incident_schedule(local(2026, 10, 5, 6, 0)) == SCHEDULE_DAY
    assert incident_schedule(local(2026, 10, 5, 17, 59)) == SCHEDULE_DAY
    assert (
        incident_schedule(local(2026, 10, 5, 18, 0)) == SCHEDULE_EVENING_NIGHT_WEEKEND
    )
    assert (
        incident_schedule(local(2026, 10, 10, 12, 0)) == SCHEDULE_EVENING_NIGHT_WEEKEND
    )


def test_colleague_deployment_stats() -> None:
    """Test a colleague's deployment percentage, separately per schedule."""
    now = local(2026, 10, 9, 20, 0)
    incidents = [
        summarize_incident(incident, USER_ID, now)
        for incident in (
            # Monday daytime: Mark went out.
            incident_with(
                1,
                local(2026, 10, 5, 10, 0),
                [response(MARK, "Mark", "acknowledged", assigned_skill_ids=[3])],
            ),
            # Monday evening: Mark was available, but rejected.
            incident_with(
                2,
                local(2026, 10, 5, 20, 0),
                [
                    response(
                        MARK,
                        "Mark",
                        "rejected",
                        available_at_incident_creation=True,
                    )
                ],
            ),
            # Tuesday evening: Mark wasn't available and didn't respond.
            incident_with(
                3, local(2026, 10, 6, 21, 0), [response(MARK, "Mark", "expired")]
            ),
            # Wednesday daytime: Mark wasn't alerted.
            incident_with(4, local(2026, 10, 7, 9, 0), []),
        )
    ]

    assert deployment_stats(incidents, now, MARK, SCHEDULE_DAY) == {
        "percentage": 100,
        "alarms": 1,
        "real_deployments": 1,
        "virtual_deployments": 0,
    }
    assert deployment_stats(incidents, now, MARK, SCHEDULE_EVENING_NIGHT_WEEKEND) == {
        "percentage": 50,
        "alarms": 2,
        "real_deployments": 0,
        "virtual_deployments": 1,
    }
    assert deployment_stats(incidents, now, NICK, SCHEDULE_DAY) is None


def test_rank_candidates() -> None:
    """Test candidates rank by response, priority group and percentage."""
    responses = [
        response(USER_ID, "Colin", "acknowledged", available_at_incident_creation=True),
        response(MARK, "Mark", "acknowledged", available_at_incident_creation=True),
        response(NICK, "Nick", "pending", on_duty=True),
        response(9, "Andy", "rejected", on_duty=True),
        response(
            10,
            "Piet",
            "acknowledged",
            on_duty=True,
        ),
        response(
            11,
            "Sanne",
            "acknowledged",
            available_at_incident_creation=True,
            group_available_at_incident_creation=True,
        ),
    ]
    ranked = rank_candidates(
        responses,
        {USER_ID, MARK, NICK, 9, 11},
        {USER_ID: 20, MARK: 40, NICK: 0, 9: 0, 11: 90},
    )

    # Sanne is in a paraat team, which beats a lower percentage. Piet isn't a
    # colleague, Andy rejected and Nick can still respond.
    assert [(c["name"], c["group"], c["percentage"]) for c in ranked] == [
        ("Sanne", "paraat", 90),
        ("Colin", "available", 20),
        ("Mark", "available", 40),
        ("Nick", "on_duty", 0),
    ]


async def test_colleagues_and_ranking(
    hass: HomeAssistant,
    mock_api: MagicMock,
    setup_integration: Callable[[], Any],
) -> None:
    """Test selecting colleagues adds their sensors and ranks the incident."""
    now = dt_util.now()
    past = [
        incident_with(
            1,
            now - timedelta(days=2),
            [
                response(USER_ID, "Colin", "acknowledged", assigned_skill_ids=[3]),
                response(MARK, "Mark", "rejected", on_duty=True),
                response(NICK, "Nick", "acknowledged"),
            ],
        ),
        incident_with(
            2,
            now - timedelta(days=1),
            [
                response(USER_ID, "Colin", "rejected"),
                response(MARK, "Mark", "acknowledged", assigned_skill_ids=[3]),
                response(NICK, "Nick", "acknowledged"),
            ],
        ),
    ]
    mock_api.get_incidents_page.side_effect = lambda page, _per_page: (
        [
            {"id": i["id"], "type": i["type"], "created_at": i["created_at"]}
            for i in past
        ]
        if page == 1
        else []
    )
    mock_api.get_incident.side_effect = {i["id"]: i for i in past}.get

    config_entry = await setup_integration()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("sensor.aspirant_ranking") is None

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    options = result["data_schema"].schema[CONF_COLLEAGUES].config["options"]
    assert [option["label"] for option in options] == ["Mark", "Nick"]

    # An incident is active when the entry reloads with the new options.
    mock_api.get_latest_incident.return_value = incident_with(
        3,
        now,
        [
            response(
                USER_ID, "Colin", "acknowledged", available_at_incident_creation=True
            ),
            response(MARK, "Mark", "acknowledged", on_duty=True),
            response(NICK, "Nick", "pending"),
        ],
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_COLLEAGUES: [str(MARK), str(NICK)]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {
        CONF_COLLEAGUES: {str(MARK): "Mark", str(NICK): "Nick"}
    }
    await hass.async_block_till_done(wait_background_tasks=True)

    schedule = incident_schedule(now - timedelta(days=1))
    mark = hass.states.get(f"sensor.deployment_percentage_mark_{schedule}")
    assert mark is not None
    assert hass.states.get(f"sensor.deployment_percentage_nick_{schedule}") is not None

    # Mark is on duty, so ranks above the user despite a deployment.
    ranking = hass.states.get("sensor.aspirant_ranking")
    assert ranking.state == "2"
    assert [c["name"] for c in ranking.attributes["candidates"]] == [
        "Mark",
        "Colin",
        "Nick",
    ]
    assert ranking.attributes["incident_id"] == 3
