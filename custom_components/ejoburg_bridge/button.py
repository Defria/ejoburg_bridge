"""Button platform for e-Joburg Bridge."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EJoburgCoordinator

PN_DOMAIN = "persistent_notification"
PN_SERVICE_CREATE = "create"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EJoburgCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            EJoburgRefreshButton(coordinator, entry),
            EJoburgRefreshTariffsButton(coordinator, entry),
            EJoburgOpenLatestStatementButton(coordinator, entry),
        ]
    )


class EJoburgRefreshButton(CoordinatorEntity[EJoburgCoordinator], ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "e-Joburg Refresh"
    _attr_suggested_object_id = "ejoburg_refresh"

    def __init__(self, coordinator: EJoburgCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_refresh"

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()


class EJoburgOpenLatestStatementButton(
    CoordinatorEntity[EJoburgCoordinator], ButtonEntity
):
    _attr_has_entity_name = True
    _attr_name = "e-Joburg Open Latest Statement"
    _attr_suggested_object_id = "ejoburg_open_latest_statement"

    def __init__(self, coordinator: EJoburgCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_open_latest_statement"

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()
        url = None
        if self.coordinator.data:
            url = self.coordinator.data.get("latest_local_pdf_url")

        if url:
            message = f"[Open latest statement]({url})"
        else:
            message = "No statement PDF is currently available."

        await self.hass.services.async_call(
            PN_DOMAIN,
            PN_SERVICE_CREATE,
            {
                "title": "e-Joburg Statement",
                "message": message,
                "notification_id": f"ejoburg_statement_{self._entry.entry_id}",
            },
            blocking=True,
        )


class EJoburgRefreshTariffsButton(CoordinatorEntity[EJoburgCoordinator], ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "e-Joburg Refresh Tariffs"
    _attr_suggested_object_id = "ejoburg_refresh_tariffs"

    def __init__(self, coordinator: EJoburgCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_refresh_tariffs"

    async def async_press(self) -> None:
        await self.coordinator.async_refresh_tariffs()
