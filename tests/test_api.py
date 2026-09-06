"""Tests for the portal API parser and account selection."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

API_PATH = Path(__file__).parents[1] / "custom_components/ejoburg_bridge/portal_api.py"
SPEC = importlib.util.spec_from_file_location("ejoburg_bridge_api", API_PATH)
assert SPEC and SPEC.loader
API = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(API)
PortalApi = API.PortalApi
EJoburgApiError = API.EJoburgApiError


STATEMENT_PAGE = """
<form id="historyForm">
  <input name="javax.faces.ViewState" value="state" />
  <select name="historyForm:account_input">
    <option value="">Select</option>
    <option value="1111111111">First</option>
    <option value="2222222222">Second</option>
  </select>
</form>
"""


class StatementHistoryTests(unittest.TestCase):
    def _api(self) -> PortalApi:
        api = PortalApi("https://example.invalid")
        api._request = lambda method, path, **kwargs: (  # type: ignore[method-assign]
            STATEMENT_PAGE
            if method == "GET"
            else (
                '<update id="historyForm:statementHistory:daPanel">'
                "<![CDATA[]]></update>"
            )
        )
        return api

    def test_selects_configured_account_instead_of_first(self) -> None:
        result = self._api().get_statement_history("2222 222 222")

        self.assertEqual(result["account_number_selected"], "2222222222")
        self.assertEqual(result["accounts"], ["1111111111", "2222222222"])

    def test_rejects_account_not_owned_by_user(self) -> None:
        with self.assertRaisesRegex(EJoburgApiError, "not available"):
            self._api().get_statement_history("3333333333")


if __name__ == "__main__":
    unittest.main()
