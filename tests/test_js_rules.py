"""The js matcher: device-registry WebSocket deprecations in Lovelace cards."""

from __future__ import annotations

import io
import json
import shutil
import tarfile

from tools.rules_engine import (
    Rule,
    dedupe_js_findings,
    load_rules,
    looks_minified_js,
    match_js_source,
    match_source,
    strip_js_comments,
)
from tools.scan import scan_repo


def _js_rules(repo_root) -> list[Rule]:
    payload = json.loads(
        (repo_root / "data" / "manual_rules.json").read_text(encoding="utf-8")
    )
    rules = load_rules(payload["rules"])
    return [r for r in rules if r.match and r.match.get("type") == "js"]


def _read_fixture(fixtures_dir, *parts) -> str:
    return fixtures_dir.joinpath("plugins", *parts).read_text(encoding="utf-8")


def test_worked_example_produces_exactly_one_finding(repo_root, fixtures_dir):
    """The brief's worked example, byte for byte the committed fixture."""
    source = _read_fixture(fixtures_dir, "config_entries_card", "power-card.js")
    findings = match_js_source("src/power-card.js", source, _js_rules(repo_root))
    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == "device-registry-config-entries-field"
    assert finding.breaks_in == "2027.8"
    assert finding.file == "src/power-card.js"
    assert finding.line == 4
    rule = next(
        r for r in _js_rules(repo_root) if r.id == "device-registry-config-entries-field"
    )
    assert rule.replacement == "config_entry_id"


def test_comment_only_mention_produces_zero_findings(repo_root, fixtures_dir):
    source = _read_fixture(fixtures_dir, "comment_only_card", "card.js")
    assert match_js_source("card.js", source, _js_rules(repo_root)) == []


def test_all_four_rules_fire_on_their_own_token(repo_root, fixtures_dir):
    rules = _js_rules(repo_root)
    ts = _read_fixture(fixtures_dir, "ts_plus_bundle", "src", "card.ts")
    ids = {f.rule_id for f in match_js_source("src/card.ts", ts, rules)}
    assert ids == {
        "device-registry-primary-config-entry-field",
        "device-registry-config-entries-subentries-field",
    }
    command = _read_fixture(fixtures_dir, "remove_command_card", "admin-card.ts")
    ids = {f.rule_id for f in match_js_source("admin-card.ts", command, rules)}
    assert ids == {"device-registry-remove-config-entry-command"}
    replacements = {
        r.id: (r.replacement, r.breaks_in) for r in rules
    }
    assert replacements == {
        "device-registry-config-entries-field": ("config_entry_id", "2027.8"),
        "device-registry-config-entries-subentries-field": ("config_subentry_id", "2027.8"),
        "device-registry-primary-config-entry-field": ("config_entry_id", "2027.8"),
        "device-registry-remove-config-entry-command": ("config/device_registry/remove", "2027.9"),
    }


def test_config_entries_token_does_not_match_the_longer_field(repo_root):
    source = 'hass.callWS({type: "config/device_registry/list"});\nd.config_entries_subentries;\n'
    ids = {f.rule_id for f in match_js_source("c.js", source, _js_rules(repo_root))}
    assert "device-registry-config-entries-field" not in ids
    assert "device-registry-config-entries-subentries-field" in ids


def test_no_websocket_context_means_no_finding(repo_root):
    source = "const entries = device.config_entries;\nfetchSomething(entries);\n"
    assert match_js_source("c.js", source, _js_rules(repo_root)) == []


def test_short_tokens_are_refused_whatever_the_rule_claims():
    rule = Rule(
        id="too-short",
        kind="js",
        symbol="id",
        message="",
        breaks_in="2027.8",
        source="",
        match={"type": "js", "token": "id"},
    )
    assert match_js_source("c.js", 'hass.callWS({type: "config/device_registry/list"}); x.id;', [rule]) == []


def test_python_matcher_never_sees_a_js_rule_and_vice_versa(repo_root):
    js_rules = _js_rules(repo_root)
    python = "class C:\n    def f(self, device):\n        return device.config_entries\n"
    assert match_source("f.py", python, js_rules) == []


def test_strip_js_comments_preserves_strings_and_line_numbers():
    source = 'const url = "https://x/y"; // config_entries here\n/* primary_config_entry */\nconst a = 1;\n'
    stripped = strip_js_comments(source)
    assert '"https://x/y"' in stripped
    assert "config_entries" not in stripped
    assert stripped.count("\n") == source.count("\n")


def test_minified_detection(fixtures_dir):
    assert looks_minified_js("dist/bundle.min.js", "const a=1;")
    long_line = _read_fixture(fixtures_dir, "minified_card", "dist-bundle.js")
    assert looks_minified_js("dist-bundle.js", long_line)
    assert not looks_minified_js("src/card.ts", "const a = 1;\n")


def test_dedupe_prefers_the_source_file_over_the_bundle(repo_root, fixtures_dir):
    rules = _js_rules(repo_root)
    findings = []
    for path in ("src/card.ts", "dist/card.js"):
        text = fixtures_dir.joinpath("plugins", "ts_plus_bundle", *path.split("/")).read_text(
            encoding="utf-8"
        )
        findings.extend(match_js_source(path, text, rules))
    deduped = dedupe_js_findings(findings)
    assert len(deduped) == 2
    assert all(f.file == "src/card.ts" for f in deduped)


def _plugin_tarball(fixtures_dir, tmp_path) -> bytes:
    root = tmp_path / "repo-1.0.0"
    shutil.copytree(fixtures_dir / "plugins" / "ts_plus_bundle", root / "code")
    shutil.copytree(fixtures_dir / "plugins" / "minified_card", root / "build")
    vendor = root / "node_modules" / "home-assistant-js-websocket"
    vendor.mkdir(parents=True)
    (vendor / "index.js").write_text(
        'export const x = (c) => c.sendMessagePromise({type: "config/device_registry/list"}); // d.config_entries\n',
        encoding="utf-8",
    )
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        archive.add(root, arcname="repo-1.0.0")
    return buffer.getvalue()


def test_plugin_repo_scan_dedupes_and_counts_skips(
    repo_root, fixtures_dir, tmp_path, monkeypatch
):
    from tools import scan as scan_module

    body = _plugin_tarball(fixtures_dir, tmp_path)
    monkeypatch.setattr(scan_module, "http_get", lambda url, **kwargs: body)
    entry = {
        "full_name": "someone/some-card",
        "category": "plugin",
        "domain": None,
        "last_version": "1.0.0",
    }
    record, findings = scan_repo(entry, _js_rules(repo_root))
    assert record["status"] == "scanned"
    assert record["category"] == "plugin"
    assert record["skipped_minified"] == 2, "the .min.js and the long-line bundle"
    assert record["skipped_vendor"] == 1
    rule_ids = sorted(f["rule_id"] for f in record["findings"])
    assert rule_ids == [
        "device-registry-config-entries-subentries-field",
        "device-registry-primary-config-entry-field",
    ]
    assert all(f["file"].endswith("src/card.ts") for f in record["findings"])


def test_card_scanner_and_report_raise_a_card_issue(repo_root, fixtures_dir, tmp_path, sample_index):
    from custom_components.breakage_radar.scanner import scan_cards
    from custom_components.breakage_radar.report import build_report

    community = tmp_path / "www" / "community"
    shutil.copytree(
        fixtures_dir / "plugins" / "config_entries_card", community / "power-card"
    )
    shutil.copytree(
        fixtures_dir / "plugins" / "minified_card", community / "bundle-only-card"
    )
    manual = json.loads(
        (repo_root / "data" / "manual_rules.json").read_text(encoding="utf-8")
    )
    rules_payload = [
        {**r, "matchable": True} for r in manual["rules"] if (r.get("match") or {}).get("type") == "js"
    ]
    scan = scan_cards(str(community), rules_payload, current_version="2026.9")
    assert scan["cards"]["power-card"]["status"] == "affected"
    assert scan["cards"]["bundle-only-card"]["status"] == "unknown"
    assert scan["skipped_minified"] == 2

    sample_index["rules"] = rules_payload
    report = build_report(
        sample_index,
        {},
        cards=["power-card", "bundle-only-card"],
        local_card_scan=scan,
        current_version="2026.9",
    )
    assert report["affected_cards"] == ["power-card"]
    assert report["cards_installed_count"] == 2
    assert "bundle-only-card" in report["cards_not_analysed"]
    detail = next(d for d in report["details"] if d["kind"] == "card")
    assert detail["domain"] == "power-card"
    assert detail["rule_id"] == "device-registry-config-entries-field"
    assert detail["source"] == "local"
    assert detail["when"] == "upcoming", "core derives the old fields until 2027.8"
    assert "power-card" in report["summarised_cards"]
    assert report["broken_now_cards"] == {}


def test_url_path_segment_is_not_the_field(repo_root):
    """Measured on a real card: ADNPolymerase/ha-pluviometer-card v0.4.2 calls
    the config-entries REST flow API from a file that also uses callWS. The
    path segment must never satisfy the field rule."""
    source = (
        'if (this._hass.callWS) {\n'
        '  const list = await this._hass.callWS({ type: "config/entity_registry/list" });\n'
        '}\n'
        'const flow = await this._hass.callApi("POST", "config/config_entries/flow", {});\n'
        'await this._hass.callApi("POST", "config/config_entries/flow/" + flow.flow_id, {});\n'
    )
    assert match_js_source("dist/card.js", source, _js_rules(repo_root)) == []


def test_a_type_declaration_is_not_a_read(repo_root):
    """Measured on a real card: MindFreeze/ha-sankey-chart v6.3.0 declares
    config_entries in its own DeviceRegistryEntry interface, in the same file
    that calls callWS, and never reads the field at runtime. A declaration or
    an object-literal key must stay silent; a real read must not."""
    rules = _js_rules(repo_root)
    declaration = (
        'export interface DeviceRegistryEntry {\n'
        '  id: string;\n'
        '  config_entries: string[];\n'
        '  primary_config_entry?: string;\n'
        '}\n'
        'export const getDevices = (hass) => hass.callWS({ type: "config/device_registry/list" });\n'
    )
    assert match_js_source("src/hass.ts", declaration, rules) == []
    read = (
        'const devices = await hass.callWS({ type: "config/device_registry/list" });\n'
        'const mine = cond ? device.config_entries : [];\n'
    )
    ids = {f.rule_id for f in match_js_source("src/hass.ts", read, rules)}
    assert ids == {"device-registry-config-entries-field"}
