"""Data update coordinator for e-Joburg Bridge."""

from __future__ import annotations

import os
import json
import csv
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EJoburgApi, EJoburgApiError
from .const import (
    CONF_ACCOUNT_NUMBER,
    CONF_BASE_URL,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    DEFAULT_VAT_RATE_PERCENT,
    DOMAIN,
    TARIFFS_APPROVED_PAGE,
    TARIFFS_ANNEXURE_FALLBACK_URL,
    TARIFFS_BOOKLET_FALLBACK_URL,
    TARIFFS_CONSOLIDATED_FALLBACK_URL,
)


class EJoburgCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(
        self, hass: HomeAssistant, entry_id: str, entry_data: dict[str, Any]
    ) -> None:
        self.entry_id = entry_id
        self._entry_data = entry_data
        self.api: EJoburgApi | None = None
        self._pdf_dir = hass.config.path("www", "ejoburg_bridge", entry_id)
        self._latest_local_pdf_url: str | None = None
        self._tariffs_json_path = os.path.join(self._pdf_dir, "tariffs_latest.json")
        self._tariffs_csv_path = os.path.join(self._pdf_dir, "tariffs_latest.csv")
        self._tariffs_csv_url = (
            f"/local/ejoburg_bridge/{self.entry_id}/tariffs_latest.csv"
        )
        self._tariffs_data: dict[str, Any] | None = None
        self._bundled_tariffs_csv_path = os.path.join(
            os.path.dirname(__file__), "coj_prepaid_electricity_tariffs_2025_26.csv"
        )

        super().__init__(
            hass,
            logger=__import__("logging").getLogger(__name__),
            name=DOMAIN,
            update_interval=timedelta(minutes=entry_data[CONF_SCAN_INTERVAL]),
        )

    async def async_login_and_prime(self) -> None:
        def _sync_init() -> None:
            self.api = EJoburgApi(self._entry_data[CONF_BASE_URL])
            self.api.login(
                self._entry_data[CONF_USERNAME], self._entry_data[CONF_PASSWORD]
            )

        await self.hass.async_add_executor_job(_sync_init)

    async def _async_update_data(self) -> dict[str, Any]:
        def _sync_load() -> dict[str, Any]:
            if self.api is None:
                self.api = EJoburgApi(self._entry_data[CONF_BASE_URL])

            self.api.login(
                self._entry_data[CONF_USERNAME], self._entry_data[CONF_PASSWORD]
            )

            overview = self.api.get_account_overview()
            payment_history = self.api.get_payment_history_summary()
            statement_history = self.api.get_statement_history()

            os.makedirs(self._pdf_dir, exist_ok=True)
            self._ensure_tariffs_loaded_once()
            rows = statement_history.get("rows", [])
            latest_pdf_meta: dict[str, Any] | None = None
            self._latest_local_pdf_url = None

            cached_statement_rows: list[dict[str, Any]] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                cached = dict(row)
                cached["download_available"] = False
                cached["local_pdf_path"] = None
                cached["local_pdf_url"] = None
                cached_statement_rows.append(cached)

            form_fields = statement_history.get("form_fields")
            form_fields_dict = form_fields if isinstance(form_fields, dict) else None

            for row in cached_statement_rows:
                button_name = str(row.get("download_button") or "").strip()
                if not button_name:
                    continue

                bill_amount = row.get("bill_amount")
                balance = row.get("balance")
                idx = row.get("index")
                idx_text = str(idx) if idx is not None else ""
                amount_text = (
                    f"{float(bill_amount):.2f}"
                    if isinstance(bill_amount, (int, float))
                    else ""
                )
                balance_text = (
                    f"{float(balance):.2f}" if isinstance(balance, (int, float)) else ""
                )
                token_parts = [
                    self._entry_data[CONF_ACCOUNT_NUMBER],
                    idx_text,
                    amount_text,
                    balance_text,
                ]
                safe_token = "_".join(
                    part.replace(".", "-") for part in token_parts if part
                )
                file_name = (
                    f"statement_{safe_token}.pdf"
                    if safe_token
                    else f"statement_{button_name}.pdf"
                )
                file_name = "".join(
                    ch if ch.isalnum() or ch in {"-", "_", "."} else "_"
                    for ch in file_name
                )
                file_path = os.path.join(self._pdf_dir, file_name)
                local_url = f"/local/ejoburg_bridge/{self.entry_id}/{file_name}"

                if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
                    pdf_bytes = self.api.download_statement_pdf(
                        button_name,
                        form_fields=form_fields_dict,
                    )
                    with open(file_path, "wb") as handle:
                        handle.write(pdf_bytes)

                row["download_available"] = True
                row["local_pdf_path"] = file_path
                row["local_pdf_url"] = local_url

                parsed: dict[str, Any] | None = None
                try:
                    with open(file_path, "rb") as handle:
                        parsed = self.api.parse_statement_pdf(handle.read())
                except Exception as exc:  # keep coordinator resilient on bad single PDF
                    self.logger.debug(
                        "Failed to parse statement PDF for row %s (%s): %s",
                        idx,
                        button_name,
                        exc,
                    )

                if isinstance(parsed, dict):
                    parsed_statement_date = parsed.get("statement_date")
                    if parsed_statement_date and not row.get("statement_date"):
                        row["statement_date"] = parsed_statement_date
                    row["pdf_parsed"] = {
                        "statement_date": parsed.get("statement_date"),
                        "due_date": parsed.get("due_date"),
                        "amount_due": parsed.get("amount_due"),
                        "amount_due_source": parsed.get("amount_due_source"),
                    }

                if self._latest_local_pdf_url is None:
                    self._latest_local_pdf_url = local_url
                    latest_pdf_meta = {
                        "button_name": button_name,
                        "local_pdf_path": file_path,
                        "local_pdf_url": local_url,
                        "parsed": parsed,
                    }

            return {
                "account_number": self._entry_data[CONF_ACCOUNT_NUMBER],
                "overview": overview,
                "payment_history": payment_history,
                "statement_history": {
                    "row_count": len(cached_statement_rows),
                    "rows": cached_statement_rows,
                    "account_number_selected": statement_history.get(
                        "account_number_selected"
                    ),
                },
                "latest_statement": latest_pdf_meta,
                "latest_local_pdf_url": self._latest_local_pdf_url,
                "tariffs": self._tariffs_data,
            }

        try:
            return await self.hass.async_add_executor_job(_sync_load)
        except EJoburgApiError as exc:
            raise UpdateFailed(str(exc)) from exc

    def _write_tariffs_csv(self, tariffs: dict[str, Any]) -> None:
        if not isinstance(tariffs, dict):
            return

        rows: list[dict[str, Any]] = []
        segments = tariffs.get("segments")
        if isinstance(segments, dict):
            prepaid = segments.get("prepaid", {})
            postpaid = segments.get("postpaid", {})
            prepaid_rows = prepaid.get("rows") if isinstance(prepaid, dict) else None
            postpaid_rows = postpaid.get("rows") if isinstance(postpaid, dict) else None
            if isinstance(prepaid_rows, list):
                rows.extend([r for r in prepaid_rows if isinstance(r, dict)])
            if isinstance(postpaid_rows, list):
                rows.extend([r for r in postpaid_rows if isinstance(r, dict)])

        if not rows:
            legacy_rows = tariffs.get("rows")
            if isinstance(legacy_rows, list):
                rows = [r for r in legacy_rows if isinstance(r, dict)]

        if not rows:
            return

        fieldnames = [
            "financial_year",
            "effective_date",
            "utility",
            "customer_segment",
            "tariff_variant",
            "tariff_structure",
            "meter_phase",
            "breaker_amp",
            "component",
            "season",
            "tou_period",
            "block",
            "usage_kwh_from",
            "usage_kwh_to",
            "usage_kwh_to_inclusive",
            "rate_c_per_kwh",
            "rate_r_per_kwh",
            "service_charge_r_per_month",
            "capacity_charge_r_per_month",
            "vat_rate_percent",
            "vat_included",
            "vat_note",
            "rate_c_per_kwh_incl_vat",
            "rate_r_per_kwh_incl_vat",
            "service_charge_r_per_month_incl_vat",
            "capacity_charge_r_per_month_incl_vat",
            "source",
        ]

        with open(self._tariffs_csv_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                if not isinstance(row, dict):
                    continue
                writer.writerow({name: row.get(name) for name in fieldnames})

    @staticmethod
    def _with_tariff_segments(tariffs: dict[str, Any]) -> dict[str, Any]:
        prepaid_rows = tariffs.get("prepaid_rows") if isinstance(tariffs, dict) else []
        if not isinstance(prepaid_rows, list):
            fallback_rows = tariffs.get("rows") if isinstance(tariffs, dict) else []
            prepaid_rows = fallback_rows if isinstance(fallback_rows, list) else []

        postpaid_rows = (
            tariffs.get("postpaid_rows") if isinstance(tariffs, dict) else []
        )
        if not isinstance(postpaid_rows, list):
            postpaid_rows = []

        postpaid_note = "Residential postpaid/conventional rows parsed from annexure."
        postpaid_status = "ready" if postpaid_rows else "unavailable"
        if postpaid_status != "ready":
            parse_error = tariffs.get("postpaid_parse_error")
            if parse_error:
                postpaid_note = f"Postpaid parse failed: {parse_error}"
            else:
                postpaid_note = (
                    "Postpaid/conventional rows are not available in this dataset."
                )

        segments = {
            "prepaid": {
                "status": "ready" if prepaid_rows else "unavailable",
                "row_count": len(prepaid_rows),
                "rows": prepaid_rows,
            },
            "postpaid": {
                "status": postpaid_status,
                "row_count": len(postpaid_rows),
                "rows": postpaid_rows,
                "note": postpaid_note,
            },
        }
        merged = dict(tariffs)
        merged["rows"] = prepaid_rows
        merged["row_count"] = len(prepaid_rows) + len(postpaid_rows)
        merged["segments"] = segments
        return merged

    def _load_tariffs_from_local_cache(self) -> dict[str, Any] | None:
        if not os.path.exists(self._tariffs_json_path):
            return None
        try:
            with open(self._tariffs_json_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        payload["local_csv_url"] = self._tariffs_csv_url
        return self._with_tariff_segments(payload)

    def _load_tariffs_from_bundled_csv(self) -> dict[str, Any] | None:
        if not os.path.exists(self._bundled_tariffs_csv_path):
            return None

        def _to_int_or_none(value: Any) -> int | None:
            if value in {None, ""}:
                return None
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return None

        def _to_float_or_zero(value: Any) -> float:
            if value in {None, ""}:
                return 0.0
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0

        def _to_bool(value: Any) -> bool:
            return str(value).strip().lower() in {"true", "1", "yes"}

        rows: list[dict[str, Any]] = []
        try:
            with open(self._bundled_tariffs_csv_path, "r", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    if not isinstance(row, dict):
                        continue
                    rows.append(
                        {
                            "financial_year": row.get("financial_year"),
                            "effective_date": row.get("effective_date"),
                            "utility": row.get("utility"),
                            "customer_segment": row.get("customer_segment"),
                            "tariff_variant": row.get("tariff_variant"),
                            "tariff_structure": row.get("tariff_structure") or None,
                            "meter_phase": row.get("meter_phase") or None,
                            "breaker_amp": _to_int_or_none(row.get("breaker_amp")),
                            "component": row.get("component") or None,
                            "season": row.get("season") or None,
                            "tou_period": row.get("tou_period") or None,
                            "block": _to_int_or_none(row.get("block")) or 0,
                            "usage_kwh_from": _to_int_or_none(
                                row.get("usage_kwh_from")
                            ),
                            "usage_kwh_to": _to_int_or_none(row.get("usage_kwh_to")),
                            "usage_kwh_to_inclusive": _to_bool(
                                row.get("usage_kwh_to_inclusive")
                            ),
                            "rate_c_per_kwh": _to_float_or_zero(
                                row.get("rate_c_per_kwh")
                            ),
                            "rate_r_per_kwh": _to_float_or_zero(
                                row.get("rate_r_per_kwh")
                            ),
                            "service_charge_r_per_month": _to_float_or_zero(
                                row.get("service_charge_r_per_month")
                            ),
                            "capacity_charge_r_per_month": _to_float_or_zero(
                                row.get("capacity_charge_r_per_month")
                            ),
                            "vat_rate_percent": _to_float_or_zero(
                                row.get("vat_rate_percent") or DEFAULT_VAT_RATE_PERCENT
                            ),
                            "vat_included": _to_bool(row.get("vat_included")),
                            "vat_note": row.get("vat_note"),
                            "rate_c_per_kwh_incl_vat": _to_float_or_zero(
                                row.get("rate_c_per_kwh_incl_vat")
                            ),
                            "rate_r_per_kwh_incl_vat": _to_float_or_zero(
                                row.get("rate_r_per_kwh_incl_vat")
                            ),
                            "service_charge_r_per_month_incl_vat": _to_float_or_zero(
                                row.get("service_charge_r_per_month_incl_vat")
                            ),
                            "capacity_charge_r_per_month_incl_vat": _to_float_or_zero(
                                row.get("capacity_charge_r_per_month_incl_vat")
                            ),
                            "source": row.get("source") or TARIFFS_BOOKLET_FALLBACK_URL,
                        }
                    )
        except Exception:
            return None

        if not rows:
            return None

        prepaid_rows = [
            row
            for row in rows
            if str(row.get("tariff_structure") or "").strip() == ""
            and "Prepaid" in str(row.get("tariff_variant") or "")
        ]
        postpaid_rows = [
            row for row in rows if str(row.get("tariff_structure") or "").strip() != ""
        ]

        tariffs = {
            "status": "ready",
            "financial_year": rows[0].get("financial_year"),
            "effective_date": rows[0].get("effective_date"),
            "vat_rate_percent": rows[0].get("vat_rate_percent"),
            "vat_included_source": False,
            "source_page_url": TARIFFS_APPROVED_PAGE,
            "booklet_pdf_url": rows[0].get("source") or TARIFFS_BOOKLET_FALLBACK_URL,
            "consolidated_pdf_url": TARIFFS_CONSOLIDATED_FALLBACK_URL,
            "annexure_pdf_url": TARIFFS_ANNEXURE_FALLBACK_URL,
            "prepaid_rows": prepaid_rows,
            "postpaid_rows": postpaid_rows,
            "local_csv_url": self._tariffs_csv_url,
            "postpaid_parse_error": None
            if postpaid_rows
            else "Bundled fallback has prepaid rows only",
            "error": "Using bundled fallback CSV",
        }
        tariffs = self._with_tariff_segments(tariffs)
        with open(self._tariffs_json_path, "w", encoding="utf-8") as handle:
            json.dump(tariffs, handle, ensure_ascii=True, indent=2)
        self._write_tariffs_csv(tariffs)
        return tariffs

    def _download_and_parse_tariffs(self) -> dict[str, Any]:
        page_bytes = EJoburgApi._fetch_external_bytes(TARIFFS_APPROVED_PAGE)
        page_html = page_bytes.decode("utf-8", errors="replace")
        pdf_links = EJoburgApi._extract_pdf_links_from_html(
            page_html, "https://joburg.org.za"
        )

        booklet_url = next(
            (link for link in pdf_links if "tariffs-booklets.pdf" in link.lower()),
            TARIFFS_BOOKLET_FALLBACK_URL,
        )
        consolidated_url = next(
            (link for link in pdf_links if "consolidated_tariffs" in link.lower()),
            TARIFFS_CONSOLIDATED_FALLBACK_URL,
        )
        annexure_url = next(
            (link for link in pdf_links if "item_03c_annexure" in link.lower()),
            TARIFFS_ANNEXURE_FALLBACK_URL,
        )

        booklet_bytes = EJoburgApi._fetch_external_bytes(booklet_url)
        parsed_prepaid = EJoburgApi.parse_prepaid_tariffs_booklet(
            booklet_bytes,
            vat_rate_percent=DEFAULT_VAT_RATE_PERCENT,
        )

        prepaid_rows = parsed_prepaid.get("rows", [])
        for row in prepaid_rows:
            if isinstance(row, dict):
                row["source"] = booklet_url

        postpaid_rows: list[dict[str, Any]] = []
        postpaid_parse_error: str | None = None
        try:
            annexure_bytes = EJoburgApi._fetch_external_bytes(annexure_url)
            parsed_postpaid = EJoburgApi.parse_postpaid_tariffs_annexure(
                annexure_bytes,
                vat_rate_percent=DEFAULT_VAT_RATE_PERCENT,
            )
            postpaid_rows = parsed_postpaid.get("rows", [])
            for row in postpaid_rows:
                if isinstance(row, dict):
                    row["source"] = annexure_url
        except Exception as exc:
            postpaid_parse_error = str(exc)
            self.logger.warning("Postpaid tariff parse failed: %s", exc)

        tariffs = {
            "status": "ready",
            "financial_year": parsed_prepaid.get("financial_year"),
            "effective_date": parsed_prepaid.get("effective_date"),
            "vat_rate_percent": parsed_prepaid.get("vat_rate_percent"),
            "vat_included_source": False,
            "source_page_url": TARIFFS_APPROVED_PAGE,
            "booklet_pdf_url": booklet_url,
            "consolidated_pdf_url": consolidated_url,
            "annexure_pdf_url": annexure_url,
            "prepaid_rows": prepaid_rows,
            "postpaid_rows": postpaid_rows,
            "local_csv_url": self._tariffs_csv_url,
            "postpaid_parse_error": postpaid_parse_error,
            "error": None,
        }
        tariffs = self._with_tariff_segments(tariffs)

        with open(self._tariffs_json_path, "w", encoding="utf-8") as handle:
            json.dump(tariffs, handle, ensure_ascii=True, indent=2)
        self._write_tariffs_csv(tariffs)
        return tariffs

    def _ensure_tariffs_loaded_once(self) -> None:
        if isinstance(self._tariffs_data, dict):
            return
        cached = self._load_tariffs_from_local_cache()
        if isinstance(cached, dict):
            self._tariffs_data = cached
            return
        try:
            self._tariffs_data = self._download_and_parse_tariffs()
            return
        except Exception as exc:
            self.logger.warning(
                "Tariff download failed, trying bundled fallback: %s", exc
            )

        fallback = self._load_tariffs_from_bundled_csv()
        if isinstance(fallback, dict):
            self._tariffs_data = fallback
            return

        raise EJoburgApiError("Unable to load tariffs from remote source or fallback")

    def _sync_refresh_tariffs(self) -> None:
        os.makedirs(self._pdf_dir, exist_ok=True)
        try:
            self._tariffs_data = self._download_and_parse_tariffs()
            return
        except Exception as exc:
            self.logger.warning("Manual tariff refresh failed: %s", exc)
            if isinstance(self._tariffs_data, dict):
                stale = dict(self._tariffs_data)
                stale["status"] = "stale"
                stale["error"] = str(exc)
                self._tariffs_data = stale
                return
            fallback = self._load_tariffs_from_bundled_csv()
            if isinstance(fallback, dict):
                self._tariffs_data = fallback
                return
            raise EJoburgApiError(f"Tariff refresh failed: {exc}") from exc

    async def async_refresh_tariffs(self) -> None:
        await self.hass.async_add_executor_job(self._sync_refresh_tariffs)
        updated = dict(self.data or {})
        updated["tariffs"] = self._tariffs_data
        self.async_set_updated_data(updated)
