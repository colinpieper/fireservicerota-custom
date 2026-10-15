"""Diagnostics support for FireServiceRota."""

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_TOKEN, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .coordinator import FireServiceConfigEntry

TO_REDACT = {
    CONF_TOKEN,
    CONF_USERNAME,
    "unique_id",
    "title",
    "body",
    "address",
    "latitude",
    "longitude",
    "formatted_address",
    "location",
    "message_to_speech_url",
    "user_name",
    "user_nickname",
    "user_photo",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: FireServiceConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    client = coordinator.client

    return async_redact_data(
        {
            "entry": entry.as_dict(),
            "on_duty": client.on_duty,
            "availability": coordinator.data,
            "incident": client.incident,
            "incident_active": client.active,
            "trigger": client.trigger,
            "response": client.response,
            "suggested_skill_ids": client.system_assigned_skill_ids,
            "response_deadline": client.response_deadline,
        },
        TO_REDACT,
    )
