"""CoJ App client for the first-party CoJ CSD JSON API.

The ``CoJAppApi`` authenticates against the official CoJ mobile platform and
retrieves account, invoice and statement data without scraping the JSF portal.

To reach the CoJ App backend, the integration needs the *app-level client
credential* that the official mobile app embeds (it obtains an API token
before user login). This value is deliberately **not** hardcoded here; the
user supplies it via the integration options (e.g. from ``secrets.yaml``).
"""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .portal_api import EJoburgApiError


AUTH_BASE_URL = "https://api.app.prod.csd-joburg.cloud/auth/auth-service-prod/"
CUSTOMER_BASE_URL = (
    "https://api.app.prod.csd-joburg.cloud/customers/customer-service-prod/"
)
ACCOUNT_BASE_URL = (
    "https://api.app.prod.csd-joburg.cloud/accounts/account-service-prod/"
)
BILL_BASE_URL = "https://api.app.prod.csd-joburg.cloud/bills/bill-service-prod/"


class CoJAppApi:
    """Client for the first-party CoJ mobile platform APIs.

    ``app_auth_password`` is the app-level client credential used to obtain
    an API token before user login. An empty value disables the client.
    """

    def __init__(
        self, app_auth_password: str = "", timeout: int = 60
    ) -> None:
        self._app_auth_password = app_auth_password
        self.timeout = timeout
        self._app_token: str | None = None
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._download_refs: dict[str, dict[str, Any]] = {}

    def _request(
        self,
        method: str,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> Any:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Accept": "application/json", "lang": "en"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"

        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(raw).get("message", raw)
            except (AttributeError, json.JSONDecodeError):
                detail = raw
            raise EJoburgApiError(
                f"CoJ App request failed ({exc.code}): {detail}"
            ) from exc
        except (OSError, URLError) as exc:
            raise EJoburgApiError(f"CoJ App request failed: {exc}") from exc

        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EJoburgApiError("CoJ App returned invalid JSON") from exc

    def _bootstrap(self) -> str:
        if not self._app_auth_password:
            raise EJoburgApiError(
                "CoJ App client credential (app_auth_password) is not configured"
            )
        response = self._request(
            "POST",
            f"{AUTH_BASE_URL}auth/v1",
            payload={
                "client_id": "csd",
                "password": self._app_auth_password,
            },
        )
        token = response.get("access_token") if isinstance(response, dict) else None
        if not isinstance(token, str) or not token:
            raise EJoburgApiError("CoJ App authentication failed")
        self._app_token = token
        return token

    def login(self, username: str, password: str) -> None:
        app_token = self._bootstrap()
        response = self._request(
            "POST",
            f"{CUSTOMER_BASE_URL}sign-in/v2",
            token=app_token,
            payload={
                "user_name": username,
                "password": password,
                "device_token": "",
                "mobile_profile_id": 0,
            },
        )
        if not isinstance(response, dict):
            raise EJoburgApiError("CoJ App login returned no data")
        access_token = response.get("accessToken")
        refresh_token = response.get("refreshToken")
        if not isinstance(access_token, str) or not access_token:
            raise EJoburgApiError(
                str(response.get("message") or "CoJ App login failed")
            )
        self._access_token = access_token
        self._refresh_token = refresh_token if isinstance(refresh_token, str) else None

    def _authenticated_request(
        self, method: str, url: str, *, payload: dict[str, Any] | None = None
    ) -> Any:
        if not self._access_token:
            raise EJoburgApiError("CoJ App client is not logged in")
        return self._request(method, url, payload=payload, token=self._access_token)

    @staticmethod
    def _as_float(value: Any) -> float | None:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.replace("R", "").replace(",", "").strip())
            except ValueError:
                return None
        return None

    @staticmethod
    def _normalise_date(value: Any) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        return value[:10].replace("-", "/")

    @staticmethod
    def _find_account(
        payload: Any, account_number: str
    ) -> tuple[dict[str, Any], Any]:
        data = payload.get("data") if isinstance(payload, dict) else None
        accounts = data.get("accounts") if isinstance(data, dict) else None
        if not isinstance(accounts, list):
            raise EJoburgApiError("CoJ App invoice response has no accounts")

        wanted = "".join(ch for ch in account_number if ch.isdigit())
        for account in accounts:
            if not isinstance(account, dict):
                continue
            candidate = "".join(
                ch
                for ch in str(account.get("account_no") or "")
                if ch.isdigit()
            )
            if candidate == wanted:
                return account, data.get("total_due_amount")
        raise EJoburgApiError(
            "Configured account is not linked to the CoJ App profile"
        )

    def get_statement_history(
        self, account_number: str | None = None
    ) -> dict[str, Any]:
        if not account_number:
            raise EJoburgApiError("Municipal account number is required")
        payload = self._authenticated_request(
            "POST",
            f"{BILL_BASE_URL}invoices/v1",
            payload={"accountNo": account_number, "monthsToRetrieve": 10},
        )
        account, total_due = self._find_account(payload, account_number)
        invoices = account.get("invoices")
        if not isinstance(invoices, list):
            invoices = []

        self._download_refs.clear()
        rows: list[dict[str, Any]] = []
        for index, invoice in enumerate(invoices):
            if not isinstance(invoice, dict):
                continue
            invoice_number = str(invoice.get("invoice_no") or "")
            year = invoice.get("year")
            month = invoice.get("month")
            download_ref = f"{account_number}:{invoice_number}:{year}:{month}"
            self._download_refs[download_ref] = {
                "accountNumber": account_number,
                "invoiceNumber": invoice_number,
                "year": year,
                "month": month,
            }
            due_amount = self._as_float(invoice.get("invoice_due_amount"))
            rows.append(
                {
                    "index": index,
                    "statement_date": self._normalise_date(
                        invoice.get("invoice_posting_date")
                    ),
                    "due_date": self._normalise_date(invoice.get("invoice_due_date")),
                    "download_button": download_ref if invoice_number else None,
                    "bill_amount": self._as_float(
                        invoice.get("current_invoice_amount")
                    ),
                    "balance": due_amount,
                    "invoice_number": invoice_number or None,
                }
            )

        return {
            "rows": rows,
            "form_fields": None,
            "panel_html": "",
            "account_number_selected": account_number,
            "accounts": [str(account.get("account_no") or account_number)],
            "total_due_amount": self._as_float(total_due),
        }

    def download_statement_pdf(
        self, download_ref: str, form_fields: dict[str, str] | None = None
    ) -> bytes:
        params = self._download_refs.get(download_ref)
        if not params:
            raise EJoburgApiError("Missing CoJ App invoice download metadata")
        pdf_response = self._authenticated_request(
            "GET",
            f"{BILL_BASE_URL}download-bill/v1?{urlencode(params)}",
        )
        pdf_url = (
            pdf_response.get("pdf_url") if isinstance(pdf_response, dict) else None
        )
        if not isinstance(pdf_url, str) or not pdf_url:
            raise EJoburgApiError("CoJ App returned no statement PDF URL")
        try:
            request = Request(pdf_url, headers={"Accept": "application/pdf"})
            with urlopen(request, timeout=self.timeout) as response:
                pdf_bytes = response.read()
        except (HTTPError, OSError, URLError) as exc:
            raise EJoburgApiError(f"Failed downloading statement PDF: {exc}") from exc
        if not pdf_bytes.startswith(b"%PDF"):
            raise EJoburgApiError("Statement response was not a valid PDF")
        return pdf_bytes
