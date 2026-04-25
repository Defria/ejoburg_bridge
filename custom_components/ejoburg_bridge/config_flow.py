"""Config flow for e-Joburg Bridge."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .api import EJoburgApi, EJoburgApiError
from .const import (
    CONF_ACCOUNT_NUMBER,
    CONF_BASE_URL,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
)


def _scan_interval_schema(default_scan_interval: int) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_SCAN_INTERVAL, default=default_scan_interval): vol.All(
                vol.Coerce(int), vol.Range(min=1440, max=44640)
            )
        }
    )


class EJoburgBridgeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return EJoburgBridgeOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(
                f"{user_input[CONF_USERNAME]}_{user_input[CONF_ACCOUNT_NUMBER]}"
            )
            self._abort_if_unique_id_configured()

            def _validate() -> None:
                api = EJoburgApi(user_input[CONF_BASE_URL])
                api.login(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
                overview = api.get_account_overview()
                if not isinstance(overview, dict):
                    raise EJoburgApiError("Unexpected response from account overview")

            try:
                await self.hass.async_add_executor_job(_validate)
            except EJoburgApiError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title="e-Joburg Bridge", data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_ACCOUNT_NUMBER): str,
                vol.Optional(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
                **_scan_interval_schema(DEFAULT_SCAN_INTERVAL_MINUTES).schema,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)


class EJoburgBridgeOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            current_username = str(
                self._config_entry.options.get(
                    CONF_USERNAME,
                    self._config_entry.data.get(CONF_USERNAME, ""),
                )
            )
            current_password = str(
                self._config_entry.options.get(
                    CONF_PASSWORD,
                    self._config_entry.data.get(CONF_PASSWORD, ""),
                )
            )
            current_base_url = str(
                self._config_entry.options.get(
                    CONF_BASE_URL,
                    self._config_entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
                )
            )

            resolved_username = str(
                user_input.get(CONF_USERNAME, current_username)
            ).strip()
            new_password = str(user_input.get(CONF_PASSWORD, ""))
            resolved_password = new_password if new_password else current_password
            resolved_base_url = str(
                user_input.get(CONF_BASE_URL, current_base_url)
            ).strip()
            resolved_interval = int(
                user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES)
            )

            merged_options = {
                CONF_USERNAME: resolved_username,
                CONF_PASSWORD: resolved_password,
                CONF_BASE_URL: resolved_base_url,
                CONF_SCAN_INTERVAL: resolved_interval,
            }

            def _validate() -> None:
                api = EJoburgApi(resolved_base_url)
                api.login(resolved_username, resolved_password)
                overview = api.get_account_overview()
                if not isinstance(overview, dict):
                    raise EJoburgApiError("Unexpected response from account overview")

            try:
                await self.hass.async_add_executor_job(_validate)
            except EJoburgApiError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title="", data=merged_options)

        current_interval = int(
            self._config_entry.options.get(
                CONF_SCAN_INTERVAL,
                self._config_entry.data.get(
                    CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES
                ),
            )
        )
        current_username = str(
            self._config_entry.options.get(
                CONF_USERNAME,
                self._config_entry.data.get(CONF_USERNAME, ""),
            )
        )
        current_base_url = str(
            self._config_entry.options.get(
                CONF_BASE_URL,
                self._config_entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
            )
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME, default=current_username): str,
                    vol.Optional(CONF_PASSWORD, default=""): str,
                    vol.Required(CONF_BASE_URL, default=current_base_url): str,
                    vol.Required(CONF_SCAN_INTERVAL, default=current_interval): vol.All(
                        vol.Coerce(int), vol.Range(min=1440, max=44640)
                    ),
                }
            ),
            errors=errors,
        )
