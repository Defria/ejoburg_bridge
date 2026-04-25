"""Sensor platform for e-Joburg Bridge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_ACCOUNT_NUMBER, DOMAIN
from .coordinator import EJoburgCoordinator


@dataclass
class EJoburgField:
    key: str
    name: str
    unit: str | None = None


LATEST_STATEMENT_AMOUNT = EJoburgField(
    "latest_statement_amount", "Latest Statement Amount", "ZAR"
)
LATEST_STATEMENT_ROW_COUNT = EJoburgField("statement_row_count", "Statement Row Count")
LATEST_STATEMENT_PDF_URL = EJoburgField(
    "latest_statement_pdf_url", "Latest Statement PDF URL"
)
ACCOUNT_NUMBER_DETECTED = EJoburgField(
    "account_number_detected", "Detected Account Number"
)
TARIFFS_STATUS = EJoburgField("tariffs_status", "Tariffs Status")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EJoburgCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            EJoburgSensor(coordinator, entry, LATEST_STATEMENT_AMOUNT),
            EJoburgSensor(coordinator, entry, LATEST_STATEMENT_ROW_COUNT),
            EJoburgSensor(coordinator, entry, LATEST_STATEMENT_PDF_URL),
            EJoburgSensor(coordinator, entry, ACCOUNT_NUMBER_DETECTED),
            EJoburgSensor(coordinator, entry, TARIFFS_STATUS),
        ]
    )


class EJoburgSensor(CoordinatorEntity[EJoburgCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EJoburgCoordinator,
        entry: ConfigEntry,
        field: EJoburgField,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._field = field
        account = entry.data[CONF_ACCOUNT_NUMBER]
        self._attr_unique_id = f"{entry.entry_id}_{field.key}_{account}"
        self._attr_name = f"e-Joburg {field.name}"
        self._attr_suggested_object_id = f"ejoburg_{field.key}"
        if field.unit:
            self._attr_native_unit_of_measurement = field.unit

    @staticmethod
    def _choose_display_amount(
        overview: dict[str, Any], parsed: dict[str, Any]
    ) -> tuple[float | None, str]:
        amount_due = overview.get("amount_due")
        outstanding_balance = overview.get("outstanding_balance")
        parsed_amount_due = parsed.get("amount_due")
        parsed_amount_guess = parsed.get("amount_guess")

        if isinstance(amount_due, (int, float)):
            if abs(float(amount_due)) > 0.004:
                return float(amount_due), "overview.amount_due"
            if (
                isinstance(outstanding_balance, (int, float))
                and abs(float(outstanding_balance)) > 0.004
            ):
                return float(outstanding_balance), "overview.outstanding_balance"
            return float(amount_due), "overview.amount_due"

        if (
            isinstance(outstanding_balance, (int, float))
            and abs(float(outstanding_balance)) > 0.004
        ):
            return float(outstanding_balance), "overview.outstanding_balance"

        if isinstance(parsed_amount_due, (int, float)):
            return float(parsed_amount_due), "latest_statement.parsed.amount_due"

        if isinstance(parsed_amount_guess, (int, float)):
            return float(parsed_amount_guess), "latest_statement.parsed.amount_guess"

        return None, "none"

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data or {}
        overview = (
            data.get("overview") if isinstance(data.get("overview"), dict) else {}
        )
        latest_statement = data.get("latest_statement") or {}
        parsed = (
            latest_statement.get("parsed") if isinstance(latest_statement, dict) else {}
        )
        if not isinstance(parsed, dict):
            parsed = {}

        if self._field.key == "latest_statement_amount":
            value, _ = self._choose_display_amount(overview, parsed)
            return value
        if self._field.key == "statement_row_count":
            statement_history = data.get("statement_history") or {}
            return statement_history.get("row_count")
        if self._field.key == "latest_statement_pdf_url":
            return data.get("latest_local_pdf_url")
        if self._field.key == "account_number_detected":
            overview = data.get("overview") or {}
            if isinstance(overview, dict):
                return overview.get("account_number_detected")
            return None
        if self._field.key == "tariffs_status":
            tariffs = data.get("tariffs") or {}
            if isinstance(tariffs, dict):
                return tariffs.get("status", "unavailable")
            return "unavailable"
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        overview = (
            data.get("overview") if isinstance(data.get("overview"), dict) else {}
        )
        payment_history = (
            data.get("payment_history")
            if isinstance(data.get("payment_history"), dict)
            else {}
        )
        statement_history = (
            data.get("statement_history")
            if isinstance(data.get("statement_history"), dict)
            else {}
        )
        latest_statement = (
            data.get("latest_statement")
            if isinstance(data.get("latest_statement"), dict)
            else {}
        )
        tariffs = data.get("tariffs") if isinstance(data.get("tariffs"), dict) else {}
        parsed = (
            latest_statement.get("parsed")
            if isinstance(latest_statement.get("parsed"), dict)
            else {}
        )

        amount_due = overview.get("amount_due") if isinstance(overview, dict) else None
        pdf_amount_due = parsed.get("amount_due") if isinstance(parsed, dict) else None
        pdf_amount_guess = (
            parsed.get("amount_guess") if isinstance(parsed, dict) else None
        )
        display_amount, display_amount_source = self._choose_display_amount(
            overview, parsed
        )

        amount_sign = "unknown"
        if isinstance(display_amount, (int, float)):
            if display_amount < 0:
                amount_sign = "credit"
            elif display_amount > 0:
                amount_sign = "debit"
            else:
                amount_sign = "settled"

        return {
            "account_number": self._entry.data[CONF_ACCOUNT_NUMBER],
            "financial": {
                "display_amount_due": display_amount,
                "display_amount_due_source": display_amount_source,
                "amount_due_sign": amount_sign,
                "overview_amount_due": amount_due,
                "overview_outstanding_balance": overview.get("outstanding_balance"),
                "pdf_amount_due": pdf_amount_due,
                "pdf_amount_guess": pdf_amount_guess,
                "pdf_amount_due_source": parsed.get("amount_due_source")
                if isinstance(parsed, dict)
                else None,
                "pdf_due_date": parsed.get("due_date")
                if isinstance(parsed, dict)
                else None,
            },
            "overview": overview,
            "payment_history": {
                "accounts": payment_history.get("accounts", []),
                "account_count": payment_history.get("account_count"),
            },
            "statement_history": {
                "row_count": statement_history.get("row_count"),
                "account_number_selected": statement_history.get(
                    "account_number_selected"
                ),
                "rows": statement_history.get("rows", []),
            },
            "latest_statement": latest_statement,
            "latest_local_pdf_url": data.get("latest_local_pdf_url"),
            "tariffs": tariffs,
        }
