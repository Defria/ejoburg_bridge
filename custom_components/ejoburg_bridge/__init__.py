"""e-Joburg Bridge integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
import voluptuous as vol

from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, PLATFORMS
from .coordinator import EJoburgCoordinator

SERVICE_REFRESH = "refresh"
SERVICE_REFRESH_TARIFFS = "refresh_tariffs"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    merged_data = dict(entry.data)
    merged_data.update(entry.options)

    coordinator = EJoburgCoordinator(hass, entry.entry_id, merged_data)
    await coordinator.async_login_and_prime()
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH):

        async def _handle_refresh(call: Any) -> None:
            entry_id = call.data.get("entry_id")
            if entry_id:
                target = hass.data[DOMAIN].get(entry_id)
                if isinstance(target, EJoburgCoordinator):
                    await target.async_request_refresh()
                return

            for item in hass.data[DOMAIN].values():
                if isinstance(item, EJoburgCoordinator):
                    await item.async_request_refresh()

        hass.services.async_register(
            DOMAIN,
            SERVICE_REFRESH,
            _handle_refresh,
            schema=vol.Schema({vol.Optional("entry_id"): cv.string}),
        )

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH_TARIFFS):

        async def _handle_refresh_tariffs(call: Any) -> None:
            entry_id = call.data.get("entry_id")
            if entry_id:
                target = hass.data[DOMAIN].get(entry_id)
                if isinstance(target, EJoburgCoordinator):
                    await target.async_refresh_tariffs()
                return

            for item in hass.data[DOMAIN].values():
                if isinstance(item, EJoburgCoordinator):
                    await item.async_refresh_tariffs()

        hass.services.async_register(
            DOMAIN,
            SERVICE_REFRESH_TARIFFS,
            _handle_refresh_tariffs,
            schema=vol.Schema({vol.Optional("entry_id"): cv.string}),
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        has_entries = any(
            isinstance(v, EJoburgCoordinator) for v in hass.data[DOMAIN].values()
        )
        if not has_entries and hass.services.has_service(DOMAIN, SERVICE_REFRESH):
            hass.services.async_remove(DOMAIN, SERVICE_REFRESH)
        if not has_entries and hass.services.has_service(
            DOMAIN, SERVICE_REFRESH_TARIFFS
        ):
            hass.services.async_remove(DOMAIN, SERVICE_REFRESH_TARIFFS)
    return unload_ok
