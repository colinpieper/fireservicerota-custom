"""Config flow for FireServiceRota."""

from collections.abc import Mapping
from typing import Any, override

from pyfireservicerota import FireServiceRota, InvalidAuthError
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_PASSWORD, CONF_TOKEN, CONF_URL, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
)

from .const import CONF_COLLEAGUES, DOMAIN, URL_LIST

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL, default="www.brandweerrooster.nl"): vol.In(URL_LIST),
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class FireServiceRotaFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle a FireServiceRota config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlowHandler:
        """Return the options flow."""
        return OptionsFlowHandler()

    def __init__(self) -> None:
        """Initialize config flow."""
        self.api = None
        self._base_url = None
        self._username = None
        self._password = None
        self._existing_entry: dict[str, Any] | None = None
        self._description_placeholders: dict[str, str] | None = None

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initiated by the user."""
        errors: dict[str, str] = {}

        if user_input is None:
            return self._show_setup_form(user_input, errors)

        return await self._validate_and_create_entry(user_input, "user")

    async def _validate_and_create_entry(self, user_input, step_id):
        """Check if config is valid and create entry if so."""
        self._password = user_input[CONF_PASSWORD]

        extra_inputs = user_input

        if self._existing_entry:
            extra_inputs = self._existing_entry

        self._username = extra_inputs[CONF_USERNAME]
        self._base_url = extra_inputs[CONF_URL]

        if self.unique_id is None:
            await self.async_set_unique_id(self._username)
            self._abort_if_unique_id_configured()

        self.api = FireServiceRota(
            base_url=self._base_url,
            username=self._username,
            password=self._password,
        )

        try:
            token_info = await self.hass.async_add_executor_job(self.api.request_tokens)
        except InvalidAuthError:
            self.api = None
            return self.async_show_form(
                step_id=step_id,
                data_schema=DATA_SCHEMA,
                errors={"base": "invalid_auth"},
            )

        data = {
            "auth_implementation": DOMAIN,
            CONF_URL: self._base_url,
            CONF_USERNAME: self._username,
            CONF_TOKEN: token_info,
        }

        if step_id == "user":
            return self.async_create_entry(title=self._username, data=data)

        entry = await self.async_set_unique_id(self.unique_id)
        self.hass.config_entries.async_update_entry(entry, data=data)
        await self.hass.config_entries.async_reload(entry.entry_id)
        return self.async_abort(reason="reauth_successful")

    def _show_setup_form(self, user_input=None, errors=None, step_id="user"):
        """Show the setup form to the user."""

        if user_input is None:
            user_input = {}

        if step_id == "user":
            schema = {
                vol.Required(CONF_URL, default="www.brandweerrooster.nl"): vol.In(
                    URL_LIST
                ),
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }
        else:
            schema = {vol.Required(CONF_PASSWORD): str}

        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(schema),
            errors=errors or {},
            description_placeholders=self._description_placeholders,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Initialise re-authentication."""
        await self.async_set_unique_id(entry_data[CONF_USERNAME])
        self._existing_entry = {**entry_data}
        self._description_placeholders = {CONF_USERNAME: entry_data[CONF_USERNAME]}
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Get new tokens for a config entry that can't authenticate."""
        if user_input is None:
            return self._show_setup_form(step_id="reauth_confirm")

        return await self._validate_and_create_entry(user_input, "reauth_confirm")


class OptionsFlowHandler(OptionsFlowWithReload):
    """Handle FireServiceRota options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select the colleagues to compare deployment percentages with."""
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")

        client = self.config_entry.runtime_data.client
        current: dict[str, str] = self.config_entry.options.get(CONF_COLLEAGUES, {})
        users = {
            str(user_id): name
            for user_id, name in self.config_entry.runtime_data.history.known_users.items()
            if user_id != client.fsr.user_id
        }
        # Keep selected colleagues that are no longer in the history.
        users = {**current, **users}

        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_COLLEAGUES: {
                        user_id: users[user_id]
                        for user_id in user_input[CONF_COLLEAGUES]
                        if user_id in users
                    }
                }
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_COLLEAGUES, default=list(current)
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=user_id, label=name)
                                for user_id, name in sorted(
                                    users.items(), key=lambda user: user[1]
                                )
                            ],
                            multiple=True,
                        )
                    )
                }
            ),
        )
