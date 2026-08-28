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
        def __init__(self):
            self.data: dict = {}

    def callback(func):  # noqa: D103
        return func

    core.HomeAssistant = HomeAssistant
    core.callback = callback

    config_entries = module("homeassistant.config_entries")

    class ConfigEntry:  # noqa: D101
        entry_id = "test-entry"
        options: dict = {}

    class ConfigFlowResult(dict):  # noqa: D101
        pass

    class _FlowBase:
        """Enough of HA's flow API to drive a flow in a test."""

        async def async_set_unique_id(self, unique_id):
            self._unique_id = unique_id

        def _abort_if_unique_id_configured(self):
            return None

        def async_show_form(self, **kwargs):
            return ConfigFlowResult(type="form", **kwargs)

        def async_create_entry(self, **kwargs):
            return ConfigFlowResult(type="create_entry", **kwargs)

    class ConfigFlow(_FlowBase):  # noqa: D101
        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__()

    class OptionsFlow(_FlowBase):  # noqa: D101
        config_entry = ConfigEntry()
        #: Home Assistant has always set this by the time a step runs.
        hass = HomeAssistant()

    config_entries.ConfigEntry = ConfigEntry
    config_entries.ConfigFlow = ConfigFlow
    config_entries.ConfigFlowResult = ConfigFlowResult
    config_entries.OptionsFlow = OptionsFlow

    selector = module("homeassistant.helpers.selector")

    class SelectSelectorMode:  # noqa: D101
        DROPDOWN = "dropdown"
        LIST = "list"

    class SelectSelectorConfig:  # noqa: D101
        def __init__(
            self,
            options=None,
            mode=None,
            translation_key=None,
            multiple=False,
            custom_value=False,
            **kwargs,
        ):
            self.options = options or []
            self.mode = mode
            self.translation_key = translation_key
            self.multiple = multiple
            self.custom_value = custom_value

    class SelectSelector:  # noqa: D101
        def __init__(self, config):
            self.config = config

        def __call__(self, value):
            return value

    selector.SelectSelector = SelectSelector
    selector.SelectSelectorConfig = SelectSelectorConfig
    selector.SelectSelectorMode = SelectSelectorMode

    try:
        import voluptuous  # noqa: F401
    except ImportError:
        # Home Assistant always ships voluptuous; a bare CI runner does not.
        vol = module("voluptuous")

        class Marker:
            def __init__(self, schema, default=None, description=None):
                self.schema = schema
                self.default = default

            def __hash__(self):
                return hash(self.schema)

            def __eq__(self, other):
                return self.schema == getattr(other, "schema", other)

        class Schema:
            def __init__(self, schema):
                self.schema = schema

        vol.Schema = Schema
        vol.Required = Marker
        vol.Optional = Marker

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

        def async_set_updated_data(self, data):
            self.data = data
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

    def async_get(hass):  # noqa: D103 - mirrors ir.async_get(hass).issues
        return types.SimpleNamespace(issues=issue_registry.created)

    issue_registry.async_get = async_get

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


@pytest.fixture(autouse=True)
def _isolated_release_cache(tmp_path, monkeypatch):
    """Keep the PyPI release lookup deterministic and offline in every test.

    The cache is pointed at a throwaway path so a real ``.cache/`` file on the
    developer's machine cannot leak in, and the fetch fails so any code path
    that reaches it takes the documented dev-minus-one fallback.
    """
    from tools import release

    def refuse(*args, **kwargs):
        raise RuntimeError("network disabled in tests")

    monkeypatch.setattr(release, "CACHE_FILE", tmp_path / "latest_release.json")
    monkeypatch.setattr(release, "http_get_json", refuse)


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def sample_index(fixtures_dir) -> dict:
    return json.loads((FIXTURES / "index_sample.json").read_text(encoding="utf-8"))


@pytest.fixture
def index_without_fixture_tracker(sample_index) -> dict:
    """The published rules, but fixture_tracker is nowhere in the index --
    the fork/non-HACS situation the local scan exists for."""
    sample_index["integrations"] = []
    sample_index["releases"] = {}
    sample_index["clean_domains"] = []
    return sample_index


class FakeCoordinator:
    """Stands in for the DataUpdateCoordinator; the sensor only reads .data."""

    def __init__(self, data, *, success=True, error=None):
        self.data = data
        self.last_update_success = success
        self.last_error = error
        self.index_url = "https://example.invalid/index.json"


def install_component(tmp_path, fixtures_dir, fixture, domain, version="0.1.0"):
    """Copy a scanner fixture into a tmp custom_components tree, with a
    manifest built on the fly.

    Manifests are built here rather than committed because hacs/default
    rejects a repository containing more than one ``manifest.json``.
    """
    import shutil

    components = tmp_path / "custom_components"
    target = components / domain
    shutil.copytree(fixtures_dir / fixture / "custom_components" / domain, target)
    (target / "manifest.json").write_text(
        json.dumps({"domain": domain, "version": version}), encoding="utf-8"
    )
    return components


def scan_components(components, index, **kwargs):
    from custom_components.breakage_radar.scanner import scan_installed

    return scan_installed(
        str(components),
        index["rules"],
        current_version=index["core_version"],
        **kwargs,
    )


@pytest.fixture(scope="session")
def shipped_rules() -> dict:
    """The rules.json produced by a real run of tools/extract_rules.py."""
    path = REPO_ROOT / "data" / "rules.json"
    if not path.exists():
        pytest.skip("data/rules.json not built yet -- run tools/extract_rules.py")
    return json.loads(path.read_text(encoding="utf-8"))
