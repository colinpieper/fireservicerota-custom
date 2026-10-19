"""Availability periods from the FireServiceRota combined schedule."""

from datetime import datetime
from typing import Any

# Interval fields kept for a period.
PERIOD_KEYS = (
    "start_time",
    "end_time",
    "available",
    "active",
    "type",
    "assigned_function_ids",
    "skill_ids",
    "comments",
)

# Period fields that must match for adjacent periods to be merged.
MERGE_KEYS = ("available", "active", "type", "assigned_function_ids", "skill_ids")


def interval_type(interval: dict[str, Any]) -> str:
    """Return which schedule determines the availability of an interval."""
    detailed = interval.get("detailed_availability") or {}
    for schedule in ("standby_duty", "exception", "recurring"):
        if detailed.get(schedule) is not None:
            return schedule
    return "unknown"


def merge_intervals(intervals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the periods of the schedule intervals in order.

    The API splits intervals at day boundaries, so adjacent intervals that
    are otherwise the same are merged into one period.
    """
    periods: list[dict[str, Any]] = []
    for interval in sorted(intervals, key=lambda i: parse_time(i["start_time"])):
        period = {key: interval.get(key) for key in PERIOD_KEYS}
        period["type"] = interval_type(interval)
        if (
            periods
            and parse_time(periods[-1]["end_time"]) == parse_time(period["start_time"])
            and all(periods[-1][key] == period[key] for key in MERGE_KEYS)
        ):
            periods[-1]["end_time"] = period["end_time"]
        else:
            periods.append(period)
    return periods


def available_stretches(periods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the uninterrupted stretches of available periods.

    Each stretch has `start_time`, `end_time`, and the `types` and `comments`
    of its periods.
    """
    stretches: list[dict[str, Any]] = []
    for period in periods:
        if not period["available"]:
            continue
        if stretches and parse_time(stretches[-1]["end_time"]) == parse_time(
            period["start_time"]
        ):
            stretch = stretches[-1]
            stretch["end_time"] = period["end_time"]
        else:
            stretch = {
                "start_time": period["start_time"],
                "end_time": period["end_time"],
                "types": [],
                "comments": [],
            }
            stretches.append(stretch)
        if period["type"] not in stretch["types"]:
            stretch["types"].append(period["type"])
        if period["comments"] and period["comments"] not in stretch["comments"]:
            stretch["comments"].append(period["comments"])
    return stretches


def availability(periods: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """Return the current period with the start of the next period.

    Without a current period only `available` is set, like the API library.
    """
    data: dict[str, Any] = {"available": False}
    for index, period in enumerate(periods):
        if parse_time(period["end_time"]) <= now:
            continue
        if parse_time(period["start_time"]) <= now:
            data = period.copy()
            following = periods[index + 1 : index + 2]
        else:
            following = [period]
        if following:
            data.update(
                {
                    "next_start_time": following[0]["start_time"],
                    "next_end_time": following[0]["end_time"],
                    "next_available": following[0]["available"],
                    "next_type": following[0]["type"],
                }
            )
        break
    return data


def parse_time(value: str) -> datetime:
    """Parse an API timestamp."""
    return datetime.fromisoformat(value)
