"""Tests for backend resolution (portal / mobile API / auto fallback)."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

PACKAGE = "custom_components.ejoburg_bridge"
ROOT = Path(__file__).parents[1] / "custom_components/ejoburg_bridge"

# Minimal Home Assistant stubs so the coordinator module can be imported
# without a full HA installation in the unit test environment.
def _class_getitem(cls, item):
    return cls


DataUpdateCoordinatorStub = type(
    "DataUpdateCoordinator",
    (),
    {"__class_getitem__": classmethod(_class_getitem)},
)

def _init_subclass(cls, **kwargs):
    return None


_config_entries_stub = types.ModuleType("config_entries")
_config_entries_stub.ConfigFlow = type(
    "ConfigFlow", (), {"__init_subclass__": classmethod(_init_subclass)}
)
_config_entries_stub.ConfigEntry = type("ConfigEntry", (), {})
_config_entries_stub.OptionsFlow = type("OptionsFlow", (), {})

for stub_name, stub_attrs in {
    "homeassistant": {"config_entries": _config_entries_stub},
    "homeassistant.components": {},
    "homeassistant.components.http": {},
    "homeassistant.components.http.auth": {"async_sign_path": lambda *a, **k: None},
    "homeassistant.core": {"HomeAssistant": type("HomeAssistant", (), {})},
    "homeassistant.data_entry_flow": {"FlowResult": type("FlowResult", (), {})},
    "homeassistant.helpers": {},
    "homeassistant.helpers.update_coordinator": {
        "DataUpdateCoordinator": DataUpdateCoordinatorStub,
        "UpdateFailed": type("UpdateFailed", (Exception,), {}),
    },
    "homeassistant.helpers.selector": {
        "SelectSelector": lambda *a, **k: None,
        "SelectSelectorConfig": dict,
    },
    "voluptuous": {
        "Schema": lambda *a, **k: None,
        "Required": lambda *a, **k: None,
        "Optional": lambda *a, **k: None,
        "Coerce": lambda *a, **k: None,
        "Range": lambda *a, **k: None,
        "All": lambda *a, **k: None,
        "In": lambda *a, **k: None,
    },
}.items():
    if stub_name in sys.modules:
        continue
    stub = types.ModuleType(stub_name)
    for attr, value in stub_attrs.items():
        setattr(stub, attr, value)
    sys.modules[stub_name] = stub

package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules.setdefault(PACKAGE, package)

for module_name in (
    "portal_api",
    "statement_parser",
    "cache",
    "const",
    "coj_app_api",
    "coordinator",
    "config_flow",
):
    spec = importlib.util.spec_from_file_location(
        f"{PACKAGE}.{module_name}", ROOT / f"{module_name}.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

CONST = sys.modules[f"{PACKAGE}.const"]
COORDINATOR = sys.modules[f"{PACKAGE}.coordinator"]
COJ_APP = sys.modules[f"{PACKAGE}.coj_app_api"]


class FakeCoordinator:
    def __init__(self, entry_data: dict):
        self._entry_data = entry_data
        self._active_backend: str | None = None

    _credentials = COORDINATOR.EJoburgCoordinator._credentials
    _backend_candidates = COORDINATOR.EJoburgCoordinator._backend_candidates


def _entry(**overrides) -> dict:
    data = {
        CONST.CONF_USERNAME: "portal_user",
        CONST.CONF_PASSWORD: "portal_pass",
        CONST.CONF_BASE_URL: "https://www.e-joburg.org.za",
        CONST.CONF_ACCOUNT_NUMBER: "123456",
        CONST.CONF_BACKEND: CONST.BACKEND_AUTO,
        CONST.CONF_APP_AUTH_PASSWORD: "app-client-secret-for-tests",
    }
    data.update(overrides)
    return data


class BackendResolutionTests(unittest.TestCase):
    def test_auto_validation_falls_back_to_portal_when_mobile_fails(self) -> None:
        """auto must try portal if mobile raises, not propagate mobile's error."""
        config_flow = sys.modules[f"{PACKAGE}.config_flow"]

        calls: list[str] = []

        class FakeMobile:
            def __init__(self, *a, **k):
                pass

            def login(self, *a, **k):
                calls.append("mobile.login")
                raise config_flow.EJoburgApiError("Invalid mobile/email or password")

            def get_statement_history(self, *a, **k):
                calls.append("mobile.history")

        class FakePortal:
            def __init__(self, *a, **k):
                calls.append("portal.init")
                self._base = a[0] if a else None

            def login(self, *a, **k):
                calls.append("portal.login")

            def get_statement_history(self, *a, **k):
                calls.append("portal.history")

        config_flow.CoJAppApi = FakeMobile
        config_flow.PortalApi = FakePortal

        user_input = {
            CONST.CONF_BACKEND: CONST.BACKEND_AUTO,
            CONST.CONF_USERNAME: "portal_user",
            CONST.CONF_PASSWORD: "portal_pass",
            CONST.CONF_BASE_URL: "https://www.e-joburg.org.za",
            CONST.CONF_APP_AUTH_PASSWORD: "app-client-secret-for-tests",
        }
        config_flow._validate_backends(user_input, "123456")  # should not raise

        self.assertEqual(
            calls,
            ["mobile.login", "portal.init", "portal.login", "portal.history"],
        )

    def test_auto_skips_mobile_when_app_credential_blank(self) -> None:
        """auto must not attempt mobile when the app credential is unset."""
        co = FakeCoordinator(
            _entry(**{CONST.CONF_APP_AUTH_PASSWORD: ""})
        )
        names = [name for name, _, _, _ in co._backend_candidates()]
        self.assertEqual(names, ["portal"])

    def test_mobile_mode_with_blank_credential_yields_no_candidate(self) -> None:
        co = FakeCoordinator(
            _entry(
                **{
                    CONST.CONF_BACKEND: CONST.BACKEND_MOBILE_API,
                    CONST.CONF_APP_AUTH_PASSWORD: "",
                }
            )
        )
        names = [name for name, _, _, _ in co._backend_candidates()]
        self.assertEqual(names, [])

    def test_portal_mode_only(self) -> None:
        co = FakeCoordinator(_entry(**{CONST.CONF_BACKEND: CONST.BACKEND_PORTAL}))
        candidates = co._backend_candidates()
        self.assertEqual([name for name, _, _, _ in candidates], ["portal"])

    def test_mobile_mode_only(self) -> None:
        co = FakeCoordinator(
            _entry(**{CONST.CONF_BACKEND: CONST.BACKEND_MOBILE_API})
        )
        candidates = co._backend_candidates()
        names = [name for name, _, _, _ in candidates]
        self.assertEqual(names, ["mobile_api"])
        mobile_client = candidates[0][1]
        self.assertIsInstance(mobile_client, COJ_APP.CoJAppApi)

    def test_auto_prefers_mobile_then_portal(self) -> None:
        co = FakeCoordinator(_entry())
        candidates = co._backend_candidates()
        names = [name for name, _, _, _ in candidates]
        self.assertEqual(names, ["mobile_api", "portal"])

    def test_active_backend_preferred(self) -> None:
        co = FakeCoordinator(_entry())
        co._active_backend = CONST.BACKEND_PORTAL
        names = [name for name, _, _, _ in co._backend_candidates()]
        self.assertEqual(names, ["portal", "mobile_api"])

        co2 = FakeCoordinator(_entry())
        co2._active_backend = CONST.BACKEND_MOBILE_API
        names = [name for name, _, _, _ in co2._backend_candidates()]
        self.assertEqual(names, ["mobile_api", "portal"])

    def test_mobile_uses_same_credentials_as_portal(self) -> None:
        co = FakeCoordinator(_entry())
        candidate = co._backend_candidates()[0]
        self.assertEqual(candidate[2], "portal_user")
        self.assertEqual(candidate[3], "portal_pass")


if __name__ == "__main__":
    unittest.main()