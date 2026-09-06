"""Data update coordinator for e-Joburg Bridge."""

from __future__ import annotations

import csv
import hashlib
import json
import os
from datetime import timedelta
from typing import Any

from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import statement_parser
from .cache import is_valid_pdf, write_pdf_atomically
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
    DATA_SOURCE_COJ_APP,
    DATA_SOURCE_PORTAL,
    DEFAULT_VAT_RATE_PERCENT,
    DOCUMENT_URL,
    DOMAIN,
    TARIFFS_APPROVED_PAGE,
    TARIFFS_ANNEXURE_FALLBACK_URL,
    TARIFFS_BOOKLET_FALLBACK_URL,
    TARIFFS_CONSOLIDATED_FALLBACK_URL,
)
from .portal_api import PortalApi, EJoburgApiError


class EJoburgCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(
        self, hass: HomeAssistant, entry_id: str, entry_data: dict[str, Any]
    ) -> None:
        self.entry_id = entry_id
        self._entry_data = entry_data
        self.api: PortalApi | CoJAppApi | None = None
        self._active_backend: str | None = None
        self._cache_dir = hass.config.path("ejoburg_bridge", entry_id)
        self._pdf_dir = os.path.join(self._cache_dir, "statements")
        self._latest_local_pdf_url: str | None = None
        self._documents: dict[str, tuple[str, str, str]] = {}
        self._tariffs_json_path = os.path.join(self._cache_dir, "tariffs_latest.json")
        self._tariffs_csv_path = os.path.join(self._cache_dir, "tariffs_latest.csv")
        self._tariffs_csv_url = DOCUMENT_URL.format(
            entry_id=self.entry_id, document_id="tariffs.csv"
        )
        self._tariffs_data: dict[str, Any] | None = None
        self._bundled_tariffs_csv_path = os.path.join(
            os.path.dirname(__file__), "coj_prepaid_electricity_tariffs_2025_26.csv"
        )
        # v2 statement-metadata path: HTML-only (no per-row pypdf parse).
        # Verified bit-exact against v1 across 24/24 rows on a live account
        # (credit and debit, with rolled-in prior balances). Default on.
        # Set EJOBURG_BRIDGE_USE_V2=0 to fall back to the legacy pypdf path.
        self._use_v2 = os.environ.get("EJOBURG_BRIDGE_USE_V2", "1") != "0"

        super().__init__(
            hass,
            logger=__import__("logging").getLogger(__name__),
            name=DOMAIN,
            update_interval=timedelta(minutes=entry_data[CONF_SCAN_INTERVAL]),
        )

    def _credentials(self) -> tuple[str, str]:
        """Return the username and password shared by every backend."""
        return (
            str(self._entry_data.get(CONF_USERNAME, "")),
            str(self._entry_data.get(CONF_PASSWORD, "")),
        )

    def _backend_candidates(
        self,
    ) -> list[tuple[str, PortalApi | CoJAppApi, str, str]]:
        """Return (backend, client, username, password) candidates in priority order."""
        backend = self._entry_data.get(CONF_BACKEND, BACKEND_PORTAL)
        portal_user, portal_pass = self._credentials()

        def _mobile_candidate() -> tuple[str, CoJAppApi, str, str]:
            return (
                BACKEND_MOBILE_API,
                CoJAppApi(
                    app_auth_password=str(
                        self._entry_data.get(CONF_APP_AUTH_PASSWORD, "")
                    )
                ),
                portal_user,
                portal_pass,
            )

        def _portal_candidate() -> tuple[str, PortalApi, str, str]:
            return (BACKEND_PORTAL, PortalApi(self._entry_data[CONF_BASE_URL]), portal_user, portal_pass)

        def _append_available(name: str) -> None:
            if name == BACKEND_MOBILE_API:
                if str(self._entry_data.get(CONF_APP_AUTH_PASSWORD, "")).strip():
                    candidates.append(_mobile_candidate())
            elif name == BACKEND_PORTAL:
                candidates.append(_portal_candidate())

        if backend == BACKEND_MOBILE_API:
            candidates = []
            _append_available(BACKEND_MOBILE_API)
            return candidates

        if backend == BACKEND_PORTAL:
            return [_portal_candidate()]

        # auto: prefer the last working backend; otherwise mobile first
        # (the mobile API is always available, matching the app's own flow),
        # with portal as the fallback for portal-only accounts.
        order = [
            self._active_backend,
            BACKEND_MOBILE_API if self._active_backend == BACKEND_PORTAL else BACKEND_PORTAL,
        ] if self._active_backend else [BACKEND_MOBILE_API, BACKEND_PORTAL]

        candidates: list[tuple[str, PortalApi | CoJAppApi, str, str]] = []
        for name in order:
            _append_available(name)
        return candidates

    async def async_login_and_prime(self) -> None:
        def _sync_init() -> None:
            last_error: Exception | None = None
            for name, client, username, password in self._backend_candidates():
                try:
                    client.login(username, password)
                    client.get_statement_history(self._entry_data[CONF_ACCOUNT_NUMBER])
                except EJoburgApiError as exc:
                    last_error = exc
                    self.logger.debug("Backend %s login failed: %s", name, exc)
                    continue
                self.api = client
                self._active_backend = name
                return
            raise EJoburgApiError(
                str(last_error) or "No usable backend for this account"
            ) from last_error

        await self.hass.async_add_executor_job(_sync_init)

    async def _async_update_data(self) -> dict[str, Any]:
        def _sync_load() -> dict[str, Any]:
            last_error: Exception | None = None
            for name, client, username, password in self._backend_candidates():
                try:
                    self.api = client
                    client.login(username, password)
                    statement_history = client.get_statement_history(
                        self._entry_data[CONF_ACCOUNT_NUMBER]
                    )
                except EJoburgApiError as exc:
                    last_error = exc
                    self.api = None
                    self.logger.debug("Backend %s failed: %s", name, exc)
                    continue
                self._active_backend = name
                break
            else:
                raise EJoburgApiError(
                    str(last_error) or "No usable backend for this account"
                ) from last_error

            available_accounts = statement_history.get("accounts", [])
            payment_history = {
                "accounts": available_accounts,
                "account_count": len(available_accounts),
            }

            if self._use_v2:
                panel_html = statement_history.get("panel_html") or ""
                if panel_html:
                    v2_rows = statement_parser.parse_statement_list_rows(panel_html)
                    if v2_rows:
                        statement_history["rows"] = v2_rows

            rows = statement_history.get("rows", [])
            latest_row = next((row for row in rows if isinstance(row, dict)), {})
            overview = {
                "account_number_detected": statement_history.get(
                    "account_number_selected"
                ),
                "statement_date": latest_row.get("statement_date"),
                "due_date": latest_row.get("due_date"),
                "outstanding_balance": latest_row.get("balance"),
                "amount_due": latest_row.get("balance"),
            }
            if statement_history.get("total_due_amount") is not None:
                overview["outstanding_balance"] = statement_history[
                    "total_due_amount"
                ]
                overview["amount_due"] = statement_history["total_due_amount"]

            os.makedirs(self._pdf_dir, mode=0o700, exist_ok=True)
            self._ensure_tariffs_loaded_once()
            documents: dict[str, tuple[str, str, str]] = {}
            if os.path.isfile(self._tariffs_csv_path):
                documents["tariffs.csv"] = (
                    self._tariffs_csv_path,
                    "text/csv",
                    "ejoburg_tariffs.csv",
                )
            self._documents = documents
            latest_pdf_meta: dict[str, Any] | None = None
            self._latest_local_pdf_url = None

            cached_statement_rows: list[dict[str, Any]] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                cached = dict(row)
                cached["download_available"] = False
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
                identity_parts = [
                    str(self._entry_data[CONF_ACCOUNT_NUMBER]),
                    str(row.get("invoice_number") or ""),
                    str(row.get("statement_date") or ""),
                    str(row.get("due_date") or ""),
                ]
                if not any(identity_parts[1:]):
                    identity_parts.extend(
                        [str(idx or ""), str(bill_amount or ""), str(balance or "")]
                    )
                document_id = hashlib.sha256(
                    "|".join(identity_parts).encode("utf-8")
                ).hexdigest()[:32]
                file_name = f"statement_{document_id}.pdf"
                file_path = os.path.join(self._pdf_dir, file_name)
                local_url = DOCUMENT_URL.format(
                    entry_id=self.entry_id, document_id=document_id
                )

                try:
                    if not is_valid_pdf(file_path):
                        pdf_bytes = self.api.download_statement_pdf(
                            button_name,
                            form_fields=form_fields_dict,
                        )
                        write_pdf_atomically(file_path, pdf_bytes)
                except (EJoburgApiError, OSError, ValueError) as exc:
                    row["download_error"] = str(exc)
                    self.logger.warning(
                        "Statement download failed for row %s: %s", idx, exc
                    )
                    continue

                row["download_available"] = True
                row["local_pdf_url"] = local_url
                row["document_id"] = document_id
                download_name = self._statement_download_name(row)
                documents[document_id] = (
                    file_path,
                    "application/pdf",
                    download_name,
                )
                self._documents = dict(documents)

                parsed: dict[str, Any] | None = None
                if self._use_v2:
                    # v2: skip pypdf entirely. List HTML's Total column has
                    # been verified bit-exact against v1's PDF amount_due
                    # across all rows (including credit rows and rows with
                    # rolled-in prior balances), so we use it as the source
                    # of pdf_parsed.amount_due. Zero extra HTTP traffic, no
                    # PDF text extraction needed.
                    list_total = row.get("balance")
                    if isinstance(list_total, (int, float)):
                        amount_due_value: float | None = float(list_total)
                        amount_due_source = "list_total"
                    else:
                        amount_due_value = None
                        amount_due_source = "unavailable"
                    parsed = {
                        "statement_date": row.get("statement_date"),
                        "due_date": row.get("due_date"),
                        "amount_due": amount_due_value,
                        "amount_due_source": amount_due_source,
                    }
                    row["pdf_parsed"] = dict(parsed)
                else:
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
                        "local_pdf_url": local_url,
                        "parsed": parsed,
                    }

            return {
                "account_number": self._entry_data[CONF_ACCOUNT_NUMBER],
                "data_source": (
                    DATA_SOURCE_COJ_APP
                    if self._active_backend == BACKEND_MOBILE_API
                    else DATA_SOURCE_PORTAL
                ),
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
            data = await self.hass.async_add_executor_job(_sync_load)
        except EJoburgApiError as exc:
            raise UpdateFailed(str(exc)) from exc

        signature_lifetime = self.update_interval + timedelta(days=2)

        def _signed(path: str | None) -> str | None:
            if not path:
                return None
            return async_sign_path(
                self.hass,
                path,
                signature_lifetime,
                use_content_user=True,
            )

        rows = data.get("statement_history", {}).get("rows", [])
        for row in rows:
            if isinstance(row, dict):
                row["local_pdf_url"] = _signed(row.get("local_pdf_url"))
        latest = data.get("latest_statement")
        if isinstance(latest, dict):
            latest["local_pdf_url"] = _signed(latest.get("local_pdf_url"))
        data["latest_local_pdf_url"] = _signed(data.get("latest_local_pdf_url"))
        tariffs = data.get("tariffs")
        if isinstance(tariffs, dict):
            tariffs["local_csv_url"] = _signed(self._tariffs_csv_url)
        return data

    def _statement_download_name(self, row: dict[str, Any]) -> str:
        account = "".join(
            ch for ch in str(self._entry_data[CONF_ACCOUNT_NUMBER]) if ch.isdigit()
        )
        invoice = "".join(
            ch for ch in str(row.get("invoice_number") or "") if ch.isalnum()
        )
        date = str(row.get("statement_date") or "").replace("/", "-")
        suffix = invoice or date or str(row.get("index") or "statement")
        return f"statement_{account}_{suffix}.pdf"

    def get_document(self, document_id: str) -> tuple[str, str, str] | None:
        """Return a registered document without exposing arbitrary paths."""
        return self._documents.get(document_id)

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
        page_bytes = PortalApi._fetch_external_bytes(TARIFFS_APPROVED_PAGE)
        page_html = page_bytes.decode("utf-8", errors="replace")
        pdf_links = PortalApi._extract_pdf_links_from_html(
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

        booklet_bytes = PortalApi._fetch_external_bytes(booklet_url)
        parsed_prepaid = PortalApi.parse_prepaid_tariffs_booklet(
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
            annexure_bytes = PortalApi._fetch_external_bytes(annexure_url)
            parsed_postpaid = PortalApi.parse_postpaid_tariffs_annexure(
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
        if self._active_backend == BACKEND_MOBILE_API:
            fallback = self._load_tariffs_from_bundled_csv()
            if isinstance(fallback, dict):
                self._tariffs_data = fallback
                return
            raise EJoburgApiError("Bundled tariff data is unavailable")
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
        os.makedirs(self._cache_dir, mode=0o700, exist_ok=True)
        if self._active_backend == BACKEND_MOBILE_API:
            fallback = self._load_tariffs_from_bundled_csv()
            if isinstance(fallback, dict):
                self._tariffs_data = fallback
                return
            raise EJoburgApiError("Bundled tariff data is unavailable")
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
        if os.path.isfile(self._tariffs_csv_path):
            self._documents["tariffs.csv"] = (
                self._tariffs_csv_path,
                "text/csv",
                "ejoburg_tariffs.csv",
            )
        updated = dict(self.data or {})
        tariffs = self._tariffs_data
        if isinstance(tariffs, dict):
            tariffs = dict(tariffs)
            tariffs["local_csv_url"] = async_sign_path(
                self.hass,
                self._tariffs_csv_url,
                self.update_interval + timedelta(days=2),
                use_content_user=True,
            )
        updated["tariffs"] = tariffs
        self.async_set_updated_data(updated)
