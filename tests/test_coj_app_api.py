"""Tests for the first-party CoJ App API adapter."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

PACKAGE = "custom_components.ejoburg_bridge"
ROOT = Path(__file__).parents[1] / "custom_components/ejoburg_bridge"

package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules.setdefault(PACKAGE, package)

for module_name in ("portal_api", "coj_app_api"):
    spec = importlib.util.spec_from_file_location(
        f"{PACKAGE}.{module_name}", ROOT / f"{module_name}.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

COJ_APP = sys.modules[f"{PACKAGE}.coj_app_api"]
CoJAppApi = COJ_APP.CoJAppApi
EJoburgApiError = sys.modules[f"{PACKAGE}.portal_api"].EJoburgApiError


class CoJAppApiTests(unittest.TestCase):
    TEST_APP_AUTH = "app-client-secret-for-tests"

    def test_login_uses_app_token_and_mobile_payload(self) -> None:
        client = CoJAppApi(app_auth_password=self.TEST_APP_AUTH)
        responses = [
            {"access_token": "app-token"},
            {"accessToken": "user-token", "refreshToken": "refresh-token"},
        ]
        with patch.object(client, "_request", side_effect=responses) as request:
            client.login("user@example.com", "password")

        self.assertEqual(client._access_token, "user-token")
        self.assertEqual(
            request.call_args_list[0].kwargs["payload"],
            {
                "client_id": "csd",
                "password": self.TEST_APP_AUTH,
            },
        )
        self.assertEqual(
            request.call_args_list[1].kwargs["payload"],
            {
                "user_name": "user@example.com",
                "password": "password",
                "device_token": "",
                "mobile_profile_id": 0,
            },
        )
        self.assertEqual(request.call_args_list[1].kwargs["token"], "app-token")

    def test_login_raises_when_app_credential_missing(self) -> None:
        client = CoJAppApi()
        with patch.object(client, "_request") as request:
            with self.assertRaisesRegex(EJoburgApiError, "not configured"):
                client.login("user@example.com", "password")
        request.assert_not_called()

    def test_statement_history_maps_mobile_invoice_response(self) -> None:
        client = CoJAppApi()
        client._access_token = "user-token"
        response = {
            "data": {
                "total_due_amount": "R 123.45",
                "accounts": [
                    {
                        "account_no": "1234567890",
                        "invoices": [
                            {
                                "invoice_no": "INV-1",
                                "invoice_posting_date": "2026-08-01T00:00:00",
                                "invoice_due_date": "2026-08-20T00:00:00",
                                "invoice_due_amount": "123.45",
                                "current_invoice_amount": 100,
                                "year": 2026,
                                "month": 8,
                            }
                        ],
                    }
                ],
            }
        }
        with patch.object(client, "_authenticated_request", return_value=response):
            result = client.get_statement_history("1234567890")

        self.assertEqual(result["total_due_amount"], 123.45)
        self.assertEqual(result["account_number_selected"], "1234567890")
        self.assertEqual(result["rows"][0]["statement_date"], "2026/08/01")
        self.assertEqual(result["rows"][0]["due_date"], "2026/08/20")
        self.assertEqual(result["rows"][0]["balance"], 123.45)
        self.assertEqual(result["rows"][0]["invoice_number"], "INV-1")

    def test_statement_history_rejects_unlinked_account(self) -> None:
        client = CoJAppApi()
        client._access_token = "user-token"
        response = {"data": {"accounts": [{"account_no": "111", "invoices": []}]}}
        with patch.object(client, "_authenticated_request", return_value=response):
            with self.assertRaisesRegex(EJoburgApiError, "not linked"):
                client.get_statement_history("222")

    def test_download_statement_uses_signed_api_url(self) -> None:
        client = CoJAppApi()
        client._access_token = "user-token"
        client._download_refs["ref"] = {
            "accountNumber": "123",
            "invoiceNumber": "INV-1",
            "year": 2026,
            "month": 8,
        }
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b"%PDF-1.7\nbody"

        with (
            patch.object(
                client,
                "_authenticated_request",
                return_value={"pdf_url": "https://documents.example/statement.pdf"},
            ) as request,
            patch.object(COJ_APP, "urlopen", return_value=response) as open_url,
        ):
            result = client.download_statement_pdf("ref")

        self.assertEqual(result, b"%PDF-1.7\nbody")
        self.assertIn("download-bill/v1?", request.call_args.args[1])
        self.assertEqual(
            open_url.call_args.args[0].full_url,
            "https://documents.example/statement.pdf",
        )


if __name__ == "__main__":
    unittest.main()
