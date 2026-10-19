"""Tests for the FireServiceRota schedule periods."""

from datetime import datetime

from custom_components.fireservicerota.schedule import (
    availability,
    available_stretches,
    merge_intervals,
)

from .conftest import SCHEDULE, make_interval


def at(time: str) -> datetime:
    """Return a time on 1 or 2 October 2026 in CEST."""
    return datetime.fromisoformat(f"2026-10-{time}:00+02:00")


def test_merge_intervals() -> None:
    """Test intervals split at midnight are merged and typed."""
    periods = merge_intervals(list(reversed(SCHEDULE)))

    assert [(p["start_time"][8:16], p["end_time"][8:16]) for p in periods] == [
        ("01T00:00", "01T08:00"),
        ("01T08:00", "01T14:00"),
        ("01T14:00", "01T18:45"),
        ("01T18:45", "02T06:00"),
        ("02T06:00", "03T00:00"),
    ]
    assert [p["type"] for p in periods] == [
        "recurring",
        "exception",
        "recurring",
        "recurring",
        "recurring",
    ]
    assert periods[1]["comments"] == "Thuis"
    assert "detailed_availability" not in periods[0]


def test_merge_keeps_different_periods() -> None:
    """Test adjacent intervals with other skills are not merged."""
    periods = merge_intervals(
        [
            make_interval("01T18:00", "02T00:00", True),
            make_interval("02T00:00", "02T06:00", True, skill_ids=[166]),
        ]
    )

    assert len(periods) == 2


def test_availability_unavailable() -> None:
    """Test the current period and next period while unavailable."""
    data = availability(merge_intervals(SCHEDULE), at("01T15:00"))

    assert data == {
        "start_time": "2026-10-01T14:00:00.000+02:00",
        "end_time": "2026-10-01T18:45:00.000+02:00",
        "available": False,
        "active": True,
        "type": "recurring",
        "assigned_function_ids": [],
        "skill_ids": [],
        "comments": None,
        "next_start_time": "2026-10-01T18:45:00.000+02:00",
        "next_end_time": "2026-10-02T06:00:00.000+02:00",
        "next_available": True,
        "next_type": "recurring",
    }


def test_availability_without_current_period() -> None:
    """Test only the next period is set in a gap of the schedule."""
    data = availability(merge_intervals(SCHEDULE[3:]), at("01T12:00"))

    assert data == {
        "available": False,
        "next_start_time": "2026-10-01T18:45:00.000+02:00",
        "next_end_time": "2026-10-02T06:00:00.000+02:00",
        "next_available": True,
        "next_type": "recurring",
    }


def test_availability_last_period() -> None:
    """Test there is no next period at the end of the schedule."""
    data = availability(merge_intervals(SCHEDULE), at("02T12:00"))

    assert data["start_time"] == "2026-10-02T06:00:00.000+02:00"
    assert "next_start_time" not in data
    assert availability([], at("02T12:00")) == {"available": False}


def test_available_stretches() -> None:
    """Test adjacent available periods of different types form one stretch."""
    periods = merge_intervals(
        [
            *SCHEDULE[:5],
            make_interval("02T06:00", "02T07:00", True, "exception", comments="Vrij"),
            make_interval("02T07:00", "03T00:00", False),
        ]
    )

    assert available_stretches(periods) == [
        {
            "start_time": "2026-10-01T08:00:00.000+02:00",
            "end_time": "2026-10-01T14:00:00.000+02:00",
            "types": ["exception"],
            "comments": ["Thuis"],
        },
        {
            "start_time": "2026-10-01T18:45:00.000+02:00",
            "end_time": "2026-10-02T07:00:00.000+02:00",
            "types": ["recurring", "exception"],
            "comments": ["Vrij"],
        },
    ]
