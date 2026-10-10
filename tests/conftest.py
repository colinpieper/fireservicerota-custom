"""Fixtures for FireServiceRota tests."""

from collections.abc import Callable, Generator
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fireservicerota.const import DOMAIN
from homeassistant.const import CONF_TOKEN, CONF_URL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

USERNAME = "my@email.address"
USER_ID = 42
NOW = "2026-10-01T10:00:00+00:00"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading the custom integration in all tests."""


@pytest.fixture(autouse=True)
def frozen_time(freezer) -> None:
    """Start every test at a fixed time."""
    freezer.move_to(NOW)


def make_incident(
    incident_id: int = 1000,
    *,
    created_at: datetime | None = None,
    state: str = "alerting",
    status: str | None = "pending",
    assigned_skill_ids: list[int] | None = None,
    respond_seconds: int = 80,
) -> dict[str, Any]:
    """Return an incident as returned by GET /incidents/{id}."""
    created = created_at or dt_util.utcnow()
    incident: dict[str, Any] = {
        "id": incident_id,
        "body": f"P 2 BLB-04 Test incident {incident_id}",
        "type": "incident_alert",
        "state": state,
        "prio": "prio2",
        "created_at": created.isoformat(),
        "can_respond_until": (created + timedelta(seconds=respond_seconds)).isoformat(),
        "task_ids": [4967],
        "address": {
            "latitude": 51.12,
            "longitude": 5.95,
            "address_type": "home",
            "formatted_address": "Velvin 13\n6065BJ Montfort\n",
        },
        "incident_responses": [],
        "incident_skill_assignments": [],
    }
    if status is not None:
        incident["incident_responses"] = [
            {"user_id": 7, "status": "acknowledged", "assigned_skill_ids": [1]},
            {
                "user_id": USER_ID,
                "user_name": "Colin",
                "status": status,
                "assigned_skill_ids": assigned_skill_ids or [],
                "reported_status": "dispatched" if assigned_skill_ids else "unknown",
            },
        ]
    if assigned_skill_ids:
        incident["incident_skill_assignments"] = [
            {"user_id": USER_ID, "skill_ids": assigned_skill_ids}
        ]
    return incident


def make_message(incident: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """Return a websocket message for an incident."""
    message = {
        key: value
        for key, value in incident.items()
        if key not in ("incident_responses", "incident_skill_assignments", "state")
    }
    message["trigger"] = "new"
    message.update(overrides)
    return message


@pytest.fixture
def mock_api() -> Generator[MagicMock]:
    """Mock the FireServiceRota API."""
    with patch(
        "custom_components.fireservicerota.coordinator.FireServiceRotaApi",
        autospec=True,
    ) as api_class:
        api = api_class.return_value
        api.user_id = USER_ID
        api.get_availability.return_value = {
            "available": True,
            "active": True,
            "type": "recurring",
            "skill_ids": [165],
        }
        api.get_latest_incident.return_value = None
        api.get_incident.return_value = None
        api.set_incident_response.return_value = None
        yield api


@pytest.fixture
def mock_websocket() -> Generator[MagicMock]:
    """Mock the incidents websocket."""
    with patch(
        "custom_components.fireservicerota.coordinator.FireServiceRotaIncidents",
        autospec=True,
    ) as ws_class:
        yield ws_class


@pytest.fixture
def send_message(
    hass: HomeAssistant, mock_websocket: MagicMock
) -> Callable[[dict[str, Any]], Any]:
    """Return a function that delivers a websocket message."""

    async def _send(message: dict[str, Any]) -> None:
        on_incident = mock_websocket.call_args.kwargs["on_incident"]
        on_incident(message)
        await hass.async_block_till_done()

    return _send


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return the config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=USERNAME,
        title=USERNAME,
        data={
            "auth_implementation": DOMAIN,
            CONF_URL: "www.brandweerrooster.nl",
            CONF_USERNAME: USERNAME,
            CONF_TOKEN: {
                "access_token": "test-access-token",
                "token_type": "Bearer",
                "expires_in": 1234,
                "refresh_token": "test-refresh-token",
                "created_at": 4321,
            },
        },
    )


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: MagicMock,
    mock_websocket: MagicMock,
) -> Callable[[], Any]:
    """Return a function that sets up the integration."""

    async def _setup() -> MockConfigEntry:
        config_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        return config_entry

    return _setup
