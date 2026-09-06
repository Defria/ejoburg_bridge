"""Config flow for e-Joburg Bridge."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .coj_app_api import CoJAppApi
from .const import (
    BACKEND_AUTO,
    BACKEND_MOBILE_API,
    BACKEND_PORTAL,
    CONF_ACCOUNT_NUMBER,
    CONF_APP_AUTH_PASSWORD,
    CONF_BACKEND,
    CONF_BASE_URL,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
)
from .portal_api import PortalApi, EJoburgApiError


def _scan_interval_schema(default_scan_interval: int) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_SCAN_INTERVAL, default=default_scan_interval): vol.All(
                vol.Coerce(int), vol.Range(min=1440, max=44640)
            )
        }
    )


def _validate_backends(user_input: dict[str, Any], account_number: str) -> None:
    """Try the configured backends and raise once none can reach the account."""
    backend = str(user_input.get(CONF_BACKEND, BACKEND_AUTO))
    portal_user = str(user_input.get(CONF_USERNAME, ""))
    portal_pass = str(user_input.get(CONF_PASSWORD, ""))
    app_auth_password = str(user_input.get(CONF_APP_AUTH_PASSWORD, "")).strip()

    def _try_mobile() -> None:
        if not app_auth_password:
            raise EJoburgApiError(
                "CoJ App client credential is not configured"
            )
        api = CoJAppApi(app_auth_password=app_auth_password)
        api.login(portal_user, portal_pass)
        api.get_statement_history(account_number)

    def _try_portal() -> None:
        api = PortalApi(user_input[CONF_BASE_URL])
        api.login(portal_user, portal_pass)
        api.get_statement_history(account_number)

    attempts: list[tuple[str, Any]] = []
    if backend == BACKEND_MOBILE_API:
        attempts = [(BACKEND_MOBILE_API, _try_mobile)]
    elif backend == BACKEND_PORTAL:
        attempts = [(BACKEND_PORTAL, _try_portal)]
    else:  # auto
        attempts = [(BACKEND_MOBILE_API, _try_mobile), (BACKEND_PORTAL, _try_portal)]
        if not app_auth_password:
            attempts = [(BACKEND_PORTAL, _try_portal)]

    last_error: Exception | None = None
    for name, attempt in attempts:
        try:
            attempt()
            return
        except EJoburgApiError as exc:
            last_error = exc
            continue

    raise EJoburgApiError(str(last_error) or "No usable backend for this account")


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
        schema = vol.Schema(
            {
                vol.Required(CONF_BACKEND, default=BACKEND_AUTO): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": BACKEND_AUTO, "label": "Automatic (recommended)"},
                            {"value": BACKEND_PORTAL, "label": "Portal only"},
                            {"value": BACKEND_MOBILE_API, "label": "CoJ App"},
                        ]
                    )
                ),
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_ACCOUNT_NUMBER): str,
                vol.Optional(CONF_APP_AUTH_PASSWORD, default=""): str,
                vol.Optional(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
                **_scan_interval_schema(DEFAULT_SCAN_INTERVAL_MINUTES).schema,
            }
        )

        if user_input is not None:
            await self.async_set_unique_id(
                f"{user_input[CONF_USERNAME]}_{user_input[CONF_ACCOUNT_NUMBER]}"
            )
            self._abort_if_unique_id_configured()

            try:
                await self.hass.async_add_executor_job(
                    lambda: _validate_backends(
                        user_input, str(user_input[CONF_ACCOUNT_NUMBER])
                    )
                )
            except EJoburgApiError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title="e-Joburg Bridge", data=user_input)

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)


class EJoburgBridgeOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
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
        current_backend = str(
            self._config_entry.options.get(
                CONF_BACKEND,
                self._config_entry.data.get(CONF_BACKEND, BACKEND_AUTO),
            )
        )
        current_app_auth_password = str(
            self._config_entry.options.get(
                CONF_APP_AUTH_PASSWORD,
                self._config_entry.data.get(CONF_APP_AUTH_PASSWORD, ""),
            )
        )

        if user_input is not None:
            resolved_username = str(
                user_input.get(CONF_USERNAME, current_username)
            ).strip()
            new_password = str(user_input.get(CONF_PASSWORD, ""))
            resolved_password = new_password if new_password else current_password
            new_app_auth = str(user_input.get(CONF_APP_AUTH_PASSWORD, ""))
            resolved_app_auth = (
                new_app_auth if new_app_auth else current_app_auth_password
            )
            resolved_base_url = str(
                user_input.get(CONF_BASE_URL, current_base_url)
            ).strip()
            resolved_backend = str(user_input.get(CONF_BACKEND, current_backend))
            resolved_interval = int(
                user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES)
            )

            merged_options = {
                CONF_USERNAME: resolved_username,
                CONF_PASSWORD: resolved_password,
                CONF_BASE_URL: resolved_base_url,
                CONF_BACKEND: resolved_backend,
                CONF_SCAN_INTERVAL: resolved_interval,
                CONF_APP_AUTH_PASSWORD: resolved_app_auth,
            }

            def _validate() -> None:
                account_number = str(
                    self._config_entry.data.get(CONF_ACCOUNT_NUMBER, "")
                )
                _validate_backends(merged_options, account_number)

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
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BACKEND, default=current_backend): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": BACKEND_AUTO, "label": "Automatic (recommended)"},
                                {"value": BACKEND_PORTAL, "label": "Portal only"},
                                {"value": BACKEND_MOBILE_API, "label": "CoJ App"},
                            ]
                        )
                    ),
                    vol.Required(CONF_USERNAME, default=current_username): str,
                    vol.Optional(CONF_PASSWORD, default=""): str,
                    vol.Optional(CONF_APP_AUTH_PASSWORD, default=""): str,
                    vol.Required(CONF_BASE_URL, default=current_base_url): str,
                    vol.Required(CONF_SCAN_INTERVAL, default=current_interval): vol.All(
                        vol.Coerce(int), vol.Range(min=1440, max=44640)
                    ),
                }
            ),
            errors=errors,
        )
