"""e-Joburg API helper for Home Assistant integration."""

from __future__ import annotations

import re
import ssl
from html import unescape
from io import BytesIO
from http.cookiejar import CookieJar
from typing import Any
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

from pypdf import PdfReader


class EJoburgApiError(Exception):
    """Raised when API communication fails."""


class EJoburgApi:
    def __init__(self, base_url: str, timeout: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._cookie_jar = CookieJar()
        self._opener = build_opener(HTTPCookieProcessor(self._cookie_jar))

    def _reset_session(self) -> None:
        self._cookie_jar = CookieJar()
        self._opener = build_opener(HTTPCookieProcessor(self._cookie_jar))

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _request_bytes(
        self,
        method: str,
        path: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        req = Request(self._url(path), data=data, method=method)
        req.add_header(
            "User-Agent",
            (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
            ),
        )
        req.add_header(
            "Accept",
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        )
        req.add_header("Accept-Language", "en-US,en;q=0.8")
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                return resp.read()
        except Exception as exc:
            raise EJoburgApiError(f"Request failed for {path}: {exc}") from exc

    @staticmethod
    def _fetch_external_bytes(url: str, timeout: int = 60) -> bytes:
        req = Request(url, method="GET")
        req.add_header(
            "User-Agent",
            (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
            ),
        )
        req.add_header("Accept", "text/html,application/pdf,*/*;q=0.8")
        try:
            context = ssl._create_unverified_context()
            with urlopen(req, timeout=timeout, context=context) as resp:
                return resp.read()
        except Exception as exc:
            raise EJoburgApiError(f"Failed fetching external URL {url}: {exc}") from exc

    @staticmethod
    def _extract_pdf_links_from_html(html: str, base_url: str) -> list[str]:
        links = re.findall(r'href=["\']([^"\']+)["\']', html, re.IGNORECASE)
        abs_links: list[str] = []
        for link in links:
            if not link.lower().endswith(".pdf"):
                continue
            if link.startswith("http://") or link.startswith("https://"):
                abs_links.append(link)
            elif link.startswith("/"):
                abs_links.append(f"{base_url.rstrip('/')}{link}")
        seen: set[str] = set()
        deduped: list[str] = []
        for link in abs_links:
            if link in seen:
                continue
            seen.add(link)
            deduped.append(link)
        return deduped

    @staticmethod
    def parse_prepaid_tariffs_booklet(
        pdf_bytes: bytes,
        *,
        vat_rate_percent: float,
    ) -> dict[str, Any]:
        try:
            reader = PdfReader(BytesIO(pdf_bytes))
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as exc:
            raise EJoburgApiError(f"Failed parsing tariffs PDF: {exc}") from exc

        compact = re.sub(r"\s+", " ", text)

        def _parse_cents(raw: str) -> float:
            return float(raw.replace(".", "").replace(",", "."))

        triplets = re.findall(
            r"([0-9]{3},[0-9]{2})\s+([0-9]{3},[0-9]{2})\s+([0-9]{3},[0-9]{2})",
            compact,
            re.IGNORECASE,
        )
        parsed_triplets = [
            [_parse_cents(v) for v in triplet]
            for triplet in triplets
            if triplet[0] not in {"314,68"}
        ]

        high_rates = [266.45, 305.64, 348.26]
        low_rates = [249.86, 305.64, 370.42]
        if high_rates not in parsed_triplets or low_rates not in parsed_triplets:
            raise EJoburgApiError(
                "Could not reliably parse FY25/26 prepaid blocks from tariff PDF"
            )

        fixed_match = re.search(
            r"service\s+charge\s+of\s+R\s*([0-9]+)\s*"
            r"and\s*(?:the\s*)?network\s+capacity\s+charge\s+of\s+R\s*([0-9]+)",
            compact,
            re.IGNORECASE,
        )
        service_high = float(fixed_match.group(1)) if fixed_match else 70.0
        capacity_high = float(fixed_match.group(2)) if fixed_match else 130.0

        financial_year = "2025/26"
        effective_date = "2025-07-01"
        vat_multiplier = 1 + (vat_rate_percent / 100.0)

        tiers = [
            (1, 0, 350, True),
            (2, 350, 500, True),
            (3, 500, None, False),
        ]

        rows: list[dict[str, Any]] = []
        for idx, from_kwh, to_kwh, to_inclusive in tiers:
            low_c = low_rates[idx - 1]
            high_c = high_rates[idx - 1]
            for variant, rate_c, service, capacity in [
                ("Prepaid Low (Indigent)", low_c, 0.0, 0.0),
                ("Prepaid High", high_c, service_high, capacity_high),
            ]:
                rate_r = round(rate_c / 100.0, 4)
                rows.append(
                    {
                        "financial_year": financial_year,
                        "effective_date": effective_date,
                        "utility": "City Power",
                        "customer_segment": "Residential",
                        "tariff_variant": variant,
                        "block": idx,
                        "usage_kwh_from": from_kwh,
                        "usage_kwh_to": to_kwh,
                        "usage_kwh_to_inclusive": to_inclusive,
                        "rate_c_per_kwh": round(rate_c, 2),
                        "rate_r_per_kwh": rate_r,
                        "service_charge_r_per_month": round(service, 2),
                        "capacity_charge_r_per_month": round(capacity, 2),
                        "vat_rate_percent": round(vat_rate_percent, 2),
                        "vat_included": False,
                        "vat_note": "Ex VAT (booklet convention)",
                        "rate_c_per_kwh_incl_vat": round(rate_c * vat_multiplier, 2),
                        "rate_r_per_kwh_incl_vat": round(rate_r * vat_multiplier, 4),
                        "service_charge_r_per_month_incl_vat": round(
                            service * vat_multiplier, 2
                        ),
                        "capacity_charge_r_per_month_incl_vat": round(
                            capacity * vat_multiplier, 2
                        ),
                    }
                )

        return {
            "financial_year": financial_year,
            "effective_date": effective_date,
            "vat_rate_percent": round(vat_rate_percent, 2),
            "rows": rows,
            "source_format": "tariffs_booklet_pdf",
        }

    @staticmethod
    def parse_postpaid_tariffs_annexure(
        pdf_bytes: bytes,
        *,
        vat_rate_percent: float,
    ) -> dict[str, Any]:
        try:
            reader = PdfReader(BytesIO(pdf_bytes))
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as exc:
            raise EJoburgApiError(f"Failed parsing tariffs PDF: {exc}") from exc

        compact = re.sub(r"\s+", " ", text)

        def _parse_decimal(raw: str) -> float:
            cleaned = raw.replace("\xa0", " ").strip().replace(" ", "")
            cleaned = cleaned.replace(".", "").replace(",", ".")
            return float(cleaned)

        def _section(start_marker: str, end_marker: str) -> str:
            start = compact.lower().find(start_marker.lower())
            if start == -1:
                raise EJoburgApiError(
                    f"Could not locate postpaid section start: {start_marker}"
                )
            end = compact.lower().find(end_marker.lower(), start)
            if end == -1:
                raise EJoburgApiError(
                    f"Could not locate postpaid section end: {end_marker}"
                )
            return compact[start:end]

        def _section_in(base_text: str, start_marker: str, end_marker: str) -> str:
            lower = base_text.lower()
            start = lower.find(start_marker.lower())
            if start == -1:
                raise EJoburgApiError(
                    f"Could not locate postpaid section start: {start_marker}"
                )
            end = lower.find(end_marker.lower(), start)
            if end == -1:
                raise EJoburgApiError(
                    f"Could not locate postpaid section end: {end_marker}"
                )
            return base_text[start:end]

        def _extract_amount(section_text: str, pattern: str, label: str) -> float:
            match = re.search(pattern, section_text, re.IGNORECASE)
            if not match:
                raise EJoburgApiError(f"Missing postpaid tariff value for {label}")
            return _parse_decimal(match.group(1))

        def _append_row(
            rows: list[dict[str, Any]],
            *,
            tariff_variant: str,
            tariff_structure: str,
            meter_phase: str,
            breaker_amp: int | None,
            component: str,
            season: str | None,
            tou_period: str | None,
            block: int,
            usage_kwh_from: int | None,
            usage_kwh_to: int | None,
            usage_kwh_to_inclusive: bool,
            rate_c_per_kwh: float,
            service_charge_r_per_month: float,
            capacity_charge_r_per_month: float,
        ) -> None:
            vat_multiplier = 1 + (vat_rate_percent / 100.0)
            rate_r_per_kwh = round(rate_c_per_kwh / 100.0, 4)
            rows.append(
                {
                    "financial_year": "2025/26",
                    "effective_date": "2025-07-01",
                    "utility": "City Power",
                    "customer_segment": "Residential Postpaid/Conventional",
                    "tariff_variant": tariff_variant,
                    "tariff_structure": tariff_structure,
                    "meter_phase": meter_phase,
                    "breaker_amp": breaker_amp,
                    "component": component,
                    "season": season,
                    "tou_period": tou_period,
                    "block": block,
                    "usage_kwh_from": usage_kwh_from,
                    "usage_kwh_to": usage_kwh_to,
                    "usage_kwh_to_inclusive": usage_kwh_to_inclusive,
                    "rate_c_per_kwh": round(rate_c_per_kwh, 2),
                    "rate_r_per_kwh": rate_r_per_kwh,
                    "service_charge_r_per_month": round(service_charge_r_per_month, 2),
                    "capacity_charge_r_per_month": round(
                        capacity_charge_r_per_month, 2
                    ),
                    "vat_rate_percent": round(vat_rate_percent, 2),
                    "vat_included": False,
                    "vat_note": "Ex VAT (annexure convention)",
                    "rate_c_per_kwh_incl_vat": round(
                        rate_c_per_kwh * vat_multiplier, 2
                    ),
                    "rate_r_per_kwh_incl_vat": round(
                        rate_r_per_kwh * vat_multiplier, 4
                    ),
                    "service_charge_r_per_month_incl_vat": round(
                        service_charge_r_per_month * vat_multiplier, 2
                    ),
                    "capacity_charge_r_per_month_incl_vat": round(
                        capacity_charge_r_per_month * vat_multiplier, 2
                    ),
                }
            )

        two_part_anchor = compact.lower().find(
            "two-part single and three phase tariffs"
        )
        if two_part_anchor == -1:
            raise EJoburgApiError(
                "Could not locate residential postpaid two-part tariff section"
            )

        single_phase_section = _section(
            "Single phase",
            "Three phase Service charge",
        )
        three_phase_section = _section(
            "Three phase Service charge",
            "Residential Conventional",
        )
        reseller_section = _section_in(
            compact,
            "Residential Conventional",
            "Two-part Time of Use Tariffs",
        )
        tou_start = compact.lower().find(
            "two-part time of use tariffs", two_part_anchor
        )
        if tou_start == -1:
            raise EJoburgApiError("Could not locate postpaid TOU section")
        first_seasonal = compact.lower().find("two-part seasonal", tou_start)
        if first_seasonal == -1:
            raise EJoburgApiError("Could not locate postpaid seasonal section")
        seasonal_start = compact.lower().find("two-part seasonal", first_seasonal + 1)
        if seasonal_start == -1:
            seasonal_start = first_seasonal
        seasonal_end = compact.lower().find("2. agricultural tariff", seasonal_start)
        if seasonal_end == -1:
            raise EJoburgApiError("Could not locate postpaid seasonal section end")

        tou_section = compact[tou_start:seasonal_start]
        seasonal_section = compact[seasonal_start:seasonal_end]

        single_service_60 = _extract_amount(
            single_phase_section,
            r"Service\s+charge\s+60\s+([0-9][0-9\s]*,[0-9]{2})",
            "single phase service charge 60A",
        )
        single_service_80 = _extract_amount(
            single_phase_section,
            r"Service\s+charge\s+80\s+([0-9][0-9\s]*,[0-9]{2})",
            "single phase service charge 80A",
        )
        single_network_60 = _extract_amount(
            single_phase_section,
            r"Network\s+charge\s+60\s+([0-9][0-9\s]*,[0-9]{2})",
            "single phase network charge 60A",
        )
        single_network_80 = _extract_amount(
            single_phase_section,
            r"Network\s+charge\s+80\s+([0-9][0-9\s]*,[0-9]{2})",
            "single phase network charge 80A",
        )

        single_blocks = [
            (1, 0, 500, True, "0 to 500"),
            (2, 501, 1000, True, "501 to 1000"),
            (3, 1001, 2000, True, "1001 to 2000"),
            (4, 2001, 3000, True, "2001 to 3000"),
            (5, 3001, None, False, "Above 3000"),
        ]
        single_rates: list[tuple[int, int | None, bool, float]] = []
        for block, from_kwh, to_kwh, inclusive, label in single_blocks:
            rate = _extract_amount(
                single_phase_section,
                rf"Energy\s+charge\s+{re.escape(label)}\s+([0-9][0-9\s]*,[0-9]{{2}})",
                f"single phase energy block {block}",
            )
            single_rates.append((from_kwh, to_kwh, inclusive, rate))

        three_service_80 = _extract_amount(
            three_phase_section,
            r"Service\s+charge\s+80\s+([0-9][0-9\s]*,[0-9]{2})",
            "three phase service charge 80A",
        )
        three_network_80 = _extract_amount(
            three_phase_section,
            r"Network\s+charge\s+80\s+([0-9][0-9\s]*,[0-9]{2})",
            "three phase network charge 80A",
        )
        three_labels = [
            (1, 0, 500, True, "0 to 500"),
            (2, 501, 1000, True, "501 to 1000"),
            (3, 1001, 2000, True, "1001 to 2000"),
            (4, 2001, 3000, True, "2001 to 3000"),
            (5, 3001, None, False, "Above 3000"),
        ]
        three_rates: list[tuple[int, int | None, bool, float]] = []
        for block, from_kwh, to_kwh, inclusive, label in three_labels:
            rate = _extract_amount(
                three_phase_section,
                rf"Energy\s+charge\s+{re.escape(label)}\s+([0-9][0-9\s]*,[0-9]{{2}})",
                f"three phase energy block {block}",
            )
            three_rates.append((from_kwh, to_kwh, inclusive, rate))

        reseller_service = _extract_amount(
            reseller_section,
            r"Service\s+charge\s+([0-9][0-9\s]*,[0-9]{2})",
            "reseller service charge",
        )
        reseller_network = _extract_amount(
            reseller_section,
            r"Network\s+charge\s+([0-9][0-9\s]*,[0-9]{2})",
            "reseller network charge",
        )
        reseller_labels = [
            (1, 0, 350, True, "0 to 350"),
            (2, 351, 500, True, "351 to 500"),
            (3, 501, None, False, ">500"),
        ]
        reseller_rates: list[tuple[int, int | None, bool, float]] = []
        for block, from_kwh, to_kwh, inclusive, label in reseller_labels:
            rate = _extract_amount(
                reseller_section,
                rf"Energy\s+charge\s+{re.escape(label)}\s+([0-9][0-9\s]*,[0-9]{{2}})",
                f"reseller energy block {block}",
            )
            reseller_rates.append((from_kwh, to_kwh, inclusive, rate))

        tou_service = _extract_amount(
            tou_section,
            r"Service\s+charge\s+([0-9][0-9\s]*,[0-9]{2})",
            "TOU service charge",
        )
        tou_network = _extract_amount(
            tou_section,
            r"Network\s+charge\s+([0-9][0-9\s]*,[0-9]{2})",
            "TOU network charge",
        )
        tou_rates = [
            ("summer", "peak", "Energy charge (Summer: PEAK)"),
            ("summer", "standard", "Energy charge (Summer: STANDARD)"),
            ("summer", "off_peak", "Energy charge (Summer: OFF-PEAK)"),
            ("winter", "peak", "Energy charge (Winter: PEAK)"),
            ("winter", "standard", "Energy charge (Winter: STANDARD)"),
            ("winter", "off_peak", "Energy charge (Winter: OFF-PEAK)"),
        ]

        seasonal_service = _extract_amount(
            seasonal_section,
            r"Service\s+charge\s+([0-9][0-9\s]*,[0-9]{2})",
            "seasonal service charge",
        )
        seasonal_network = _extract_amount(
            seasonal_section,
            r"Network\s+charge\s+([0-9][0-9\s]*,[0-9]{2})",
            "seasonal network charge",
        )
        seasonal_blocks = [
            (1, 0, 500, True, "0 to 500"),
            (2, 501, 1000, True, "501 to 1000"),
            (3, 1001, 2000, True, "1001 to 2000"),
            (4, 2001, 3000, True, "2001 to 3000"),
            (5, 3001, None, False, "Above 3000"),
        ]

        rows: list[dict[str, Any]] = []

        for breaker_amp, service, network in [
            (60, single_service_60, single_network_60),
            (80, single_service_80, single_network_80),
        ]:
            for idx, (from_kwh, to_kwh, inclusive, rate) in enumerate(
                single_rates, start=1
            ):
                _append_row(
                    rows,
                    tariff_variant=f"Residential Conventional Two-Part Single Phase ({breaker_amp}A)",
                    tariff_structure="two_part",
                    meter_phase="single",
                    breaker_amp=breaker_amp,
                    component="energy_block",
                    season=None,
                    tou_period=None,
                    block=idx,
                    usage_kwh_from=from_kwh,
                    usage_kwh_to=to_kwh,
                    usage_kwh_to_inclusive=inclusive,
                    rate_c_per_kwh=rate,
                    service_charge_r_per_month=service,
                    capacity_charge_r_per_month=network,
                )

        for idx, (from_kwh, to_kwh, inclusive, rate) in enumerate(three_rates, start=1):
            _append_row(
                rows,
                tariff_variant="Residential Conventional Two-Part Three Phase (80A)",
                tariff_structure="two_part",
                meter_phase="three",
                breaker_amp=80,
                component="energy_block",
                season=None,
                tou_period=None,
                block=idx,
                usage_kwh_from=from_kwh,
                usage_kwh_to=to_kwh,
                usage_kwh_to_inclusive=inclusive,
                rate_c_per_kwh=rate,
                service_charge_r_per_month=three_service_80,
                capacity_charge_r_per_month=three_network_80,
            )

        for idx, (from_kwh, to_kwh, inclusive, rate) in enumerate(
            reseller_rates, start=1
        ):
            _append_row(
                rows,
                tariff_variant="Residential Conventional Reseller",
                tariff_structure="reseller_conventional",
                meter_phase="mixed",
                breaker_amp=None,
                component="energy_block",
                season=None,
                tou_period=None,
                block=idx,
                usage_kwh_from=from_kwh,
                usage_kwh_to=to_kwh,
                usage_kwh_to_inclusive=inclusive,
                rate_c_per_kwh=rate,
                service_charge_r_per_month=reseller_service,
                capacity_charge_r_per_month=reseller_network,
            )

        for idx, (season, tou_period, label) in enumerate(tou_rates, start=1):
            rate = _extract_amount(
                tou_section,
                rf"{re.escape(label)}\s+([0-9][0-9\s]*,[0-9]{{2}})",
                f"TOU {season} {tou_period}",
            )
            _append_row(
                rows,
                tariff_variant="Residential Conventional Two-Part TOU (Three Phase 80A)",
                tariff_structure="two_part_tou",
                meter_phase="three",
                breaker_amp=80,
                component="energy_tou",
                season=season,
                tou_period=tou_period,
                block=idx,
                usage_kwh_from=None,
                usage_kwh_to=None,
                usage_kwh_to_inclusive=False,
                rate_c_per_kwh=rate,
                service_charge_r_per_month=tou_service,
                capacity_charge_r_per_month=tou_network,
            )

        for idx, (_block_no, from_kwh, to_kwh, inclusive, label) in enumerate(
            seasonal_blocks, start=1
        ):
            summer_rate = _extract_amount(
                seasonal_section,
                rf"Summer\s+Energy\s+charge\s+{re.escape(label)}\s+([0-9][0-9\s]*,[0-9]{{2}})",
                f"seasonal summer block {idx}",
            )
            _append_row(
                rows,
                tariff_variant="Residential Conventional Two-Part Seasonal (Three Phase 80A)",
                tariff_structure="two_part_seasonal",
                meter_phase="three",
                breaker_amp=80,
                component="energy_seasonal",
                season="summer",
                tou_period=None,
                block=idx,
                usage_kwh_from=from_kwh,
                usage_kwh_to=to_kwh,
                usage_kwh_to_inclusive=inclusive,
                rate_c_per_kwh=summer_rate,
                service_charge_r_per_month=seasonal_service,
                capacity_charge_r_per_month=seasonal_network,
            )

        for idx, (_block_no, from_kwh, to_kwh, inclusive, label) in enumerate(
            seasonal_blocks, start=1
        ):
            winter_rate = _extract_amount(
                seasonal_section,
                rf"Winter\s+Energy\s+charge\s+{re.escape(label)}\s+([0-9][0-9\s]*,[0-9]{{2}})",
                f"seasonal winter block {idx}",
            )
            _append_row(
                rows,
                tariff_variant="Residential Conventional Two-Part Seasonal (Three Phase 80A)",
                tariff_structure="two_part_seasonal",
                meter_phase="three",
                breaker_amp=80,
                component="energy_seasonal",
                season="winter",
                tou_period=None,
                block=idx,
                usage_kwh_from=from_kwh,
                usage_kwh_to=to_kwh,
                usage_kwh_to_inclusive=inclusive,
                rate_c_per_kwh=winter_rate,
                service_charge_r_per_month=seasonal_service,
                capacity_charge_r_per_month=seasonal_network,
            )

        if len(rows) < 20:
            raise EJoburgApiError(
                "Could not reliably parse FY25/26 residential postpaid tariffs from annexure"
            )

        return {
            "financial_year": "2025/26",
            "effective_date": "2025-07-01",
            "vat_rate_percent": round(vat_rate_percent, 2),
            "rows": rows,
            "source_format": "tariffs_annexure_pdf",
        }

    def _request(
        self,
        method: str,
        path: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        return self._request_bytes(method, path, data=data, headers=headers).decode(
            "utf-8", errors="replace"
        )

    def _extract_form(self, html: str, form_id: str) -> tuple[str, dict[str, str]]:
        form_match = re.search(
            rf'(<form[^>]*id=["\']{re.escape(form_id)}["\'][^>]*>.*?</form>)',
            html,
            re.IGNORECASE | re.DOTALL,
        )
        if not form_match:
            raise EJoburgApiError(f"Could not find form '{form_id}'")

        form_html = form_match.group(1)
        action_match = re.search(
            r"<form[^>]*action=[\"']([^\"']*)[\"']", form_html, re.IGNORECASE
        )
        action = action_match.group(1) if action_match else ""

        fields: dict[str, str] = {}
        for tag in re.findall(r"<input[^>]*>", form_html, re.IGNORECASE):
            name_match = re.search(r"\bname=[\"']([^\"']+)[\"']", tag, re.IGNORECASE)
            if not name_match:
                continue
            name = name_match.group(1)
            value_match = re.search(r"\bvalue=[\"']([^\"']*)[\"']", tag, re.IGNORECASE)
            fields[name] = unescape(value_match.group(1)) if value_match else ""

        return action, fields

    def _extract_login_contexts(self, html: str) -> list[dict[str, Any]]:
        form_blocks = re.findall(
            r"(<form[^>]*>.*?</form>)", html, re.IGNORECASE | re.DOTALL
        )
        if not form_blocks:
            raise EJoburgApiError("Could not find any login form")

        contexts: list[dict[str, Any]] = []
        for block in form_blocks:
            inputs = re.findall(r"<input[^>]*>", block, re.IGNORECASE)
            buttons = re.findall(r"<button[^>]*>", block, re.IGNORECASE)
            parsed_inputs: list[dict[str, str]] = []
            for tag in inputs:

                def _attr(name: str) -> str:
                    m = re.search(rf"\b{name}=[\"']([^\"']*)[\"']", tag, re.IGNORECASE)
                    return m.group(1) if m else ""

                name = _attr("name")
                if not name:
                    continue
                parsed_inputs.append(
                    {
                        "name": name,
                        "id": _attr("id"),
                        "type": (_attr("type") or "text").lower(),
                        "value": unescape(_attr("value")),
                    }
                )

            for tag in buttons:

                def _btn_attr(name: str) -> str:
                    m = re.search(rf"\b{name}=[\"']([^\"']*)[\"']", tag, re.IGNORECASE)
                    return m.group(1) if m else ""

                name = _btn_attr("name") or _btn_attr("id")
                if not name:
                    continue
                parsed_inputs.append(
                    {
                        "name": name,
                        "id": _btn_attr("id"),
                        "type": "button",
                        "value": unescape(_btn_attr("value") or name),
                    }
                )

            if not parsed_inputs:
                continue

            user_candidates = [
                i["name"]
                for i in parsed_inputs
                if i["type"] in {"text", "email"}
                and re.search(r"user|email|login|inputtext", i["name"], re.IGNORECASE)
            ]
            pass_candidates = [
                i["name"] for i in parsed_inputs if i["type"] == "password"
            ]
            button_candidates = [
                i["name"]
                for i in parsed_inputs
                if i["type"] in {"submit", "button", "image"}
                or re.search(
                    r"commandbutton|login|submit|sign", i["name"], re.IGNORECASE
                )
            ]

            if not user_candidates or not pass_candidates:
                continue

            form_id_match = re.search(
                r"<form[^>]*id=[\"']([^\"']+)[\"']", block, re.IGNORECASE
            )
            form_id = form_id_match.group(1) if form_id_match else ""
            if not form_id:
                continue

            fields = {i["name"]: i["value"] for i in parsed_inputs}
            user_name = user_candidates[0]
            pass_name = pass_candidates[0]
            button_name = button_candidates[0] if button_candidates else ""
            contexts.append(
                {
                    "form_id": form_id,
                    "user_name": user_name,
                    "pass_name": pass_name,
                    "button_name": button_name,
                    "fields": fields,
                }
            )

        if not contexts:
            raise EJoburgApiError("Could not locate username/password login form")

        contexts.sort(
            key=lambda c: (
                0 if str(c.get("form_id", "")).startswith("j_idt22") else 1,
                len(str(c.get("form_id", ""))),
            )
        )
        return contexts

    def _attempt_login_with_context(
        self,
        username: str,
        password: str,
        form_id: str,
        user_name: str,
        pass_name: str,
        button_name: str,
        fields: dict[str, str],
    ) -> bool:
        fields = dict(fields)
        if user_name not in fields or pass_name not in fields:
            return False

        if not button_name:
            for key in fields:
                if re.search(r"commandbutton|login|submit|sign", key, re.IGNORECASE):
                    button_name = key
                    break

        view_state = fields.get("javax.faces.ViewState", "")
        if not view_state:
            return False

        base_pairs: list[tuple[str, str]] = [
            (form_id, form_id),
            (user_name, username),
            (pass_name, password),
            ("javax.faces.ViewState", view_state),
        ]

        ajax_pairs = list(base_pairs)
        if button_name:
            ajax_pairs.extend(
                [
                    ("javax.faces.source", button_name),
                    ("javax.faces.partial.event", "click"),
                    ("javax.faces.partial.execute", f"{button_name} {form_id}"),
                    ("javax.faces.partial.render", form_id),
                    ("javax.faces.behavior.event", "click"),
                    ("javax.faces.partial.ajax", "true"),
                ]
            )
        else:
            ajax_pairs.extend(
                [
                    ("javax.faces.partial.execute", form_id),
                    ("javax.faces.partial.render", form_id),
                    ("javax.faces.partial.ajax", "true"),
                ]
            )
        ajax_body = urlencode(ajax_pairs).encode("utf-8")
        self._request(
            "POST",
            "/login",
            data=ajax_body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Faces-Request": "partial/ajax",
                "Referer": self._url("/login"),
            },
        )

        full_pairs: list[tuple[str, str]] = []
        if button_name:
            full_pairs.extend(
                [
                    ("javax.faces.partial.ajax", "true"),
                    ("javax.faces.source", button_name),
                    ("javax.faces.partial.execute", "@all"),
                    ("javax.faces.partial.render", form_id),
                    (button_name, button_name),
                    (form_id, form_id),
                    (user_name, username),
                    (pass_name, password),
                    ("javax.faces.ViewState", view_state),
                ]
            )
        else:
            full_pairs.extend(
                [
                    ("javax.faces.partial.ajax", "true"),
                    ("javax.faces.partial.execute", "@all"),
                    ("javax.faces.partial.render", form_id),
                    (form_id, form_id),
                    (user_name, username),
                    (pass_name, password),
                    ("javax.faces.ViewState", view_state),
                ]
            )

        full_body = urlencode(full_pairs).encode("utf-8")
        self._request(
            "POST",
            "/login",
            data=full_body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Faces-Request": "partial/ajax",
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/xml, text/xml, */*; q=0.01",
                "Referer": self._url("/login"),
            },
        )

        home = self._request("GET", "/home")
        if "Logout" in home:
            return True

        if button_name:
            non_ajax_pairs = list(base_pairs)
            non_ajax_pairs.append((button_name, "Login"))
            non_ajax_body = urlencode(non_ajax_pairs).encode("utf-8")
            self._request(
                "POST",
                "/login",
                data=non_ajax_body,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": self._url("/login"),
                },
            )
            home = self._request("GET", "/home")
            if "Logout" in home:
                return True

        return False

    def login(self, username: str, password: str) -> None:
        self._reset_session()
        login_page = self._request("GET", "/login")
        contexts = self._extract_login_contexts(login_page)

        tried: list[str] = []
        for context in contexts:
            form_id = str(context.get("form_id", ""))
            tried.append(form_id)
            ok = self._attempt_login_with_context(
                username=username,
                password=password,
                form_id=form_id,
                user_name=str(context.get("user_name", "")),
                pass_name=str(context.get("pass_name", "")),
                button_name=str(context.get("button_name", "")),
                fields=dict(context.get("fields", {})),
            )
            if ok:
                return

            self._reset_session()
            login_page = self._request("GET", "/login")
            contexts = self._extract_login_contexts(login_page)

        tried_text = ", ".join(tried) if tried else "none"
        raise EJoburgApiError(
            f"Login failed or session not authenticated (forms tried: {tried_text})"
        )

    def _extract_first_money(self, text: str) -> float | None:
        m = re.search(r"([+-]?R\s?[0-9][0-9,]*\.[0-9]{2})", text)
        if not m:
            return None
        value = m.group(1).replace("R", "").replace(" ", "").replace(",", "")
        try:
            return float(value)
        except ValueError:
            return None

    def _extract_input_fields(self, html: str) -> dict[str, str]:
        fields: dict[str, str] = {}
        for tag in re.findall(r"<input[^>]*>", html, re.IGNORECASE):
            name_match = re.search(r'\bname="([^"]+)"', tag, re.IGNORECASE)
            if not name_match:
                continue
            value_match = re.search(r'\bvalue="([^"]*)"', tag, re.IGNORECASE)
            fields[name_match.group(1)] = value_match.group(1) if value_match else ""
        return fields

    def _extract_statement_rows(self, html: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        command_buttons = re.findall(
            r'name="(historyForm:statementHistory:j_idt126:(\d+):[^"]*:commandButton)"',
            html,
            re.IGNORECASE,
        )
        if not command_buttons:
            return rows

        button_by_index: dict[int, str] = {}
        for full_name, idx_str in command_buttons:
            idx = int(idx_str)
            prev = button_by_index.get(idx)
            if prev is None:
                button_by_index[idx] = full_name
                continue
            if ":j_idt168:" in full_name:
                button_by_index[idx] = full_name

        for index in sorted(button_by_index):
            button_name = button_by_index[index]
            value_matches = re.findall(
                rf'name="historyForm:statementHistory:j_idt126:{index}:[^"]*_input"[^>]*value="([^"]*)"',
                html,
                re.IGNORECASE,
            )
            statement_date = None
            for raw in value_matches:
                raw_clean = (raw or "").strip()
                if re.fullmatch(r"\d{4}/\d{2}/\d{2}", raw_clean):
                    statement_date = raw_clean
                    break
                if re.fullmatch(r"\d{2}-\d{2}-\d{4}", raw_clean):
                    statement_date = raw_clean
                    break

            if statement_date is None:
                row_match = re.search(
                    rf"(<tr[^>]*>.*?{re.escape(button_name)}.*?</tr>)",
                    html,
                    re.IGNORECASE | re.DOTALL,
                )
                if row_match:
                    row_html = row_match.group(1)
                    date_match = re.search(
                        r"(\d{4}/\d{2}/\d{2}|\d{2}-\d{2}-\d{4})",
                        row_html,
                        re.IGNORECASE,
                    )
                    if date_match:
                        statement_date = date_match.group(1)

            parsed_values: list[float] = []
            for raw in value_matches:
                if raw == "":
                    continue
                try:
                    parsed_values.append(float(raw.replace(",", "")))
                except ValueError:
                    continue

            bill_amount = parsed_values[0] if parsed_values else None
            balance = parsed_values[1] if len(parsed_values) > 1 else None
            rows.append(
                {
                    "index": index,
                    "statement_date": statement_date,
                    "download_button": button_name,
                    "bill_amount": bill_amount,
                    "balance": balance,
                }
            )
        return rows

    def get_account_overview(self) -> dict[str, Any]:
        html = self._request("GET", "/account-manager")
        account_number_match = re.search(
            r"account-manager-acc-num[^>]*>\s*(\d{6,12})\s*<",
            html,
            re.IGNORECASE,
        )

        due_dates_match = re.search(
            r"(\d{2}-\d{2}-\d{4})\s*-\s*(\d{2}-\d{2}-\d{4})",
            html,
            re.IGNORECASE,
        )

        outstanding_match = re.search(
            r"Outstanding\s*Balance.*?account-manager-R\">R</label>\s*"
            r"<label[^>]*>([+-]?[0-9][0-9,]*\.[0-9]{2})</label>",
            html,
            re.IGNORECASE | re.DOTALL,
        )
        amount_due_match = re.search(
            r"Amount\s*Due.*?account-manager-R\">R</label>\s*"
            r"<label[^>]*>([+-]?[0-9][0-9,]*\.[0-9]{2})</label>",
            html,
            re.IGNORECASE | re.DOTALL,
        )

        outstanding_balance = None
        if outstanding_match:
            try:
                outstanding_balance = float(outstanding_match.group(1).replace(",", ""))
            except ValueError:
                outstanding_balance = None

        amount_due = None
        if amount_due_match:
            try:
                amount_due = float(amount_due_match.group(1).replace(",", ""))
            except ValueError:
                amount_due = None

        return {
            "account_number_detected": account_number_match.group(1)
            if account_number_match
            else None,
            "statement_date": due_dates_match.group(1) if due_dates_match else None,
            "due_date": due_dates_match.group(2) if due_dates_match else None,
            "outstanding_balance": outstanding_balance,
            "amount_due": amount_due,
        }

    def get_payment_history_summary(self) -> dict[str, Any]:
        html = self._request("GET", "/payment-history")
        account_values = sorted(
            {
                m
                for m in re.findall(
                    r'<option\s+value="(\d{6,12})"', html, re.IGNORECASE
                )
                if m != "0"
            }
        )
        return {
            "accounts": account_values,
            "account_count": len(account_values),
        }

    def get_statement_history(self) -> dict[str, Any]:
        html = self._request("GET", "/statement-history")
        if "historyForm" not in html:
            raise EJoburgApiError("Could not load statement history form")

        _, fields = self._extract_form(html, "historyForm")
        view_state = fields.get("javax.faces.ViewState")
        if not view_state:
            raise EJoburgApiError("Missing javax.faces.ViewState in statement history")

        select_match = re.search(
            r'<select[^>]*name="([^"]+_input)"[^>]*>(.*?)</select>',
            html,
            re.IGNORECASE | re.DOTALL,
        )
        if not select_match:
            return {
                "view_state": view_state,
                "rows": [],
                "form_fields": fields,
                "account_number_selected": None,
            }

        select_name = select_match.group(1)
        select_base = select_name[: -len("_input")]
        options = re.findall(
            r'<option[^>]*value="([^"]*)"[^>]*>',
            select_match.group(2),
            re.IGNORECASE,
        )
        selected_account = next((o for o in options if o.strip()), None)
        if not selected_account:
            return {
                "view_state": view_state,
                "rows": [],
                "form_fields": fields,
                "account_number_selected": None,
            }

        ajax_payload = [
            ("historyForm", "historyForm"),
            (f"{select_base}_focus", ""),
            (select_name, selected_account),
            ("javax.faces.ViewState", view_state),
            ("javax.faces.source", select_base),
            ("javax.faces.partial.event", "valueChange"),
            ("javax.faces.partial.execute", select_base),
            ("javax.faces.partial.render", "historyForm:statementHistory:daPanel"),
            ("javax.faces.behavior.event", "valueChange"),
            ("javax.faces.partial.ajax", "true"),
        ]
        ajax_body = urlencode(ajax_payload).encode("utf-8")
        ajax_response = self._request(
            "POST",
            "/statement-history",
            data=ajax_body,
            headers={
                "Faces-Request": "partial/ajax",
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/xml, text/xml, */*; q=0.01",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Referer": self._url("/statement-history"),
            },
        )

        update_match = re.search(
            r'<update id="historyForm:statementHistory:daPanel"><!\[CDATA\[(.*?)\]\]></update>',
            ajax_response,
            re.IGNORECASE | re.DOTALL,
        )
        panel_html = update_match.group(1) if update_match else ""

        merged_fields = dict(fields)
        merged_fields.update(self._extract_input_fields(panel_html))
        merged_fields["historyForm"] = "historyForm"
        merged_fields[select_name] = selected_account
        merged_fields[f"{select_base}_focus"] = ""

        rows = self._extract_statement_rows(panel_html)
        return {
            "view_state": view_state,
            "rows": rows,
            "form_fields": merged_fields,
            "account_number_selected": selected_account,
        }

    def download_statement_pdf(
        self, button_name: str, form_fields: dict[str, str] | None = None
    ) -> bytes:
        if form_fields is None:
            context = self.get_statement_history()
            fields = dict(context["form_fields"])
        else:
            fields = dict(form_fields)
        fields[button_name] = button_name

        body = urlencode(list(fields.items())).encode("utf-8")
        pdf_bytes = self._request_bytes(
            "POST",
            "/statement-history",
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": self._url("/statement-history"),
            },
        )

        if not pdf_bytes.startswith(b"%PDF"):
            raise EJoburgApiError("Statement response was not a valid PDF")
        return pdf_bytes

    def parse_statement_pdf(self, pdf_bytes: bytes) -> dict[str, Any]:
        try:
            reader = PdfReader(BytesIO(pdf_bytes))
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as exc:
            raise EJoburgApiError(f"Failed parsing statement PDF: {exc}") from exc

        compact = re.sub(r"\s+", " ", text).strip()
        lines = [line.strip() for line in text.splitlines() if line.strip()]

        account_match = re.search(r"Account\s*Number\s*:\s*(\d{6,12})", compact)
        if not account_match:
            account_match = re.search(r"\b(\d{6,12})\b", compact)

        def _parse_money(raw: str) -> float | None:
            try:
                return float(raw.replace("R", "").replace(" ", "").replace(",", ""))
            except ValueError:
                return None

        amount_due = None
        amount_due_source = "none"
        due_date = None
        statement_date = None

        statement_date_match = re.search(
            r"\bDate\s+(\d{4}/\d{2}/\d{2})\b",
            compact,
            re.IGNORECASE,
        )
        if statement_date_match:
            statement_date = statement_date_match.group(1)

        # Best signal: remittance block where Total Due and Due Date are adjacent.
        remittance_block_match = re.search(
            r"Total\s+Due\s+Due\s+Date\s+([+-]?\d[\d,]*\.\d{2})\s+(\d{4}/\d{2}/\d{2})",
            compact,
            re.IGNORECASE,
        )
        if remittance_block_match:
            amount_due = _parse_money(remittance_block_match.group(1))
            due_date = remittance_block_match.group(2)
            amount_due_source = "remittance_total_due"

        # Fallback: line-based search around "Total Due" labels.
        if amount_due is None:
            for i, line in enumerate(lines):
                if "total due" not in line.lower():
                    continue
                window = lines[i : i + 8]
                window_text = " ".join(window)
                amount_match = re.search(r"([+-]?\d[\d,]*\.\d{2})", window_text)
                date_match = re.search(r"(\d{4}/\d{2}/\d{2})", window_text)
                if amount_match:
                    amount_due = _parse_money(amount_match.group(1))
                    amount_due_source = "line_window_total_due"
                if date_match:
                    due_date = date_match.group(1)
                if amount_due is not None:
                    break

        # Last resort: amount next to OUTSTANDING table trail.
        if amount_due is None:
            outstanding_match = re.search(
                r"TOTAL\s+AMOUNT\s+OUTSTANDING\s+[\d,\.\-]+\s+[\d,\.\-]+\s+[\d,\.\-]+\s+([+-]?\d[\d,]*\.\d{2})",
                compact,
                re.IGNORECASE,
            )
            if outstanding_match:
                amount_due = _parse_money(outstanding_match.group(1))
                amount_due_source = "outstanding_table"

        # Legacy fallback for compatibility.
        amount_guess = amount_due
        if amount_guess is None:
            rand_amount_match = re.search(r"([+-]?R\s?[0-9][0-9,]*\.[0-9]{2})", compact)
            if rand_amount_match:
                amount_guess = _parse_money(rand_amount_match.group(1))
                amount_due_source = "first_rand_amount"

        return {
            "account_number": account_match.group(1) if account_match else None,
            "amount_guess": amount_guess,
            "amount_due": amount_due,
            "amount_due_source": amount_due_source,
            "statement_date": statement_date,
            "due_date": due_date,
            "text_excerpt": compact[:1200],
        }
