"""Shared test configuration.

The Home Assistant integration is tested without installing Home Assistant. When
``homeassistant`` is not importable, minimal stand-ins for the handful of symbols
``sensor.py`` needs are registered in :data:`sys.modules`, so the *real shipped
sensor class* is exercised rather than a copy of its logic. When Home Assistant
*is* installed, the real package is used and the stubs are skipped entirely.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _install_homeassistant_stubs() -> None:
    """Register the smallest possible fake ``homeassistant`` package."""

    def module(name: str) -> types.ModuleType:
        mod = types.ModuleType(name)
        sys.modules[name] = mod
        return mod

    ha = module("homeassistant")
    ha.__path__ = []  # mark as a package

    core = module("homeassistant.core")

    class HomeAssistant:  # noqa: D101
        pass

    def callback(func):  # noqa: D103
        return func

    core.HomeAssistant = HomeAssistant
    core.callback = callback

    config_entries = module("homeassistant.config_entries")

    class ConfigEntry:  # noqa: D101
        entry_id = "test-entry"

    class ConfigFlowResult(dict):  # noqa: D101
        pass

    class ConfigFlow:  # noqa: D101
        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__()

    config_entries.ConfigEntry = ConfigEntry
    config_entries.ConfigFlow = ConfigFlow
    config_entries.ConfigFlowResult = ConfigFlowResult

    const = module("homeassistant.const")

    class Platform:  # noqa: D101
        SENSOR = "sensor"

    const.Platform = Platform
    const.CONF_HOST = "host"

    components = module("homeassistant.components")
    components.__path__ = []

    sensor_mod = module("homeassistant.components.sensor")

    class SensorEntity:  # noqa: D101
        _attr_has_entity_name = False

    class SensorStateClass:  # noqa: D101
        MEASUREMENT = "measurement"

    sensor_mod.SensorEntity = SensorEntity
    sensor_mod.SensorStateClass = SensorStateClass

    device_tracker_mod = module("homeassistant.components.device_tracker")

    class ScannerEntity:  # noqa: D101
        pass

    class TrackerEntity:  # noqa: D101
        pass

    class DeviceScanner:  # noqa: D101
        pass

    device_tracker_mod.ScannerEntity = ScannerEntity
    device_tracker_mod.TrackerEntity = TrackerEntity
    device_tracker_mod.DeviceScanner = DeviceScanner

    helpers = module("homeassistant.helpers")
    helpers.__path__ = []

    entity_platform = module("homeassistant.helpers.entity_platform")
    entity_platform.AddEntitiesCallback = object

    device_registry = module("homeassistant.helpers.device_registry")

    class DeviceEntryType:  # noqa: D101
        SERVICE = "service"

    class DeviceInfo(dict):  # noqa: D101
        def __init__(self, **kwargs):
            super().__init__(**kwargs)

    device_registry.DeviceEntryType = DeviceEntryType
    device_registry.DeviceInfo = DeviceInfo

    update_coordinator = module("homeassistant.helpers.update_coordinator")

    class UpdateFailed(Exception):  # noqa: D101
        pass

    class DataUpdateCoordinator:  # noqa: D101
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, hass=None, logger=None, name=None, update_interval=None):
            self.hass = hass
            self.data = None
            self.last_update_success = True

    class CoordinatorEntity:  # noqa: D101
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, coordinator):
            self.coordinator = coordinator

        @property
        def available(self) -> bool:
            return self.coordinator.last_update_success

    update_coordinator.UpdateFailed = UpdateFailed
    update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    update_coordinator.CoordinatorEntity = CoordinatorEntity

    issue_registry = module("homeassistant.helpers.issue_registry")

    class IssueSeverity:  # noqa: D101
        WARNING = "warning"
        ERROR = "error"

    issue_registry.IssueSeverity = IssueSeverity
    issue_registry.created = {}
    issue_registry.deleted = []

    def async_create_issue(hass, domain, issue_id, **kwargs):  # noqa: D103
        issue_registry.created[(domain, issue_id)] = kwargs

    def async_delete_issue(hass, domain, issue_id):  # noqa: D103
        issue_registry.created.pop((domain, issue_id), None)
        issue_registry.deleted.append((domain, issue_id))

    issue_registry.async_create_issue = async_create_issue
    issue_registry.async_delete_issue = async_delete_issue

    try:
        import aiohttp  # noqa: F401
    except ImportError:
        # Home Assistant always ships aiohttp; a bare CI runner does not.
        aiohttp_stub = module("aiohttp")

        class ClientError(Exception):
            pass

        class ClientTimeout:
            def __init__(self, total=None):
                self.total = total

        aiohttp_stub.ClientError = ClientError
        aiohttp_stub.ClientTimeout = ClientTimeout

    aiohttp_client = module("homeassistant.helpers.aiohttp_client")
    aiohttp_client.async_get_clientsession = lambda hass: None


try:  # pragma: no cover - depends on the environment
    import homeassistant  # noqa: F401
except ImportError:
    _install_homeassistant_stubs()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-network",
        action="store_true",
        default=False,
        help="also run tests that hit the real network",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "network: test requires internet access")


def pytest_collection_modifyitems(config: pytest.Config, items) -> None:
    if config.getoption("--run-network"):
        return
    skip = pytest.mark.skip(reason="needs --run-network")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def shipped_rules() -> dict:
    """The rules.json produced by a real run of tools/extract_rules.py."""
    path = REPO_ROOT / "data" / "rules.json"
    if not path.exists():
        pytest.skip("data/rules.json not built yet -- run tools/extract_rules.py")
    return json.loads(path.read_text(encoding="utf-8"))
