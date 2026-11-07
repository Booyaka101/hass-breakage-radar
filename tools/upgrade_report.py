"""Plan a target Home Assistant release from downloaded Radar diagnostics.

Runs offline. This reads an existing scan, not the installed files themselves.
Exit 0 means no known findings due by the target, 1 means findings, and 2 means
incomplete coverage or invalid input. None of these proves compatibility.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def release_key(value: str) -> tuple[int, int, int]:
    """Accept stable HA releases only; never guess at beta/dev ordering."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}\.[0-9]{1,2}(?:\.[0-9]+)?", value):
        raise ValueError(f"not a stable Home Assistant release: {value!r}")
    parts = [int(part) for part in value.split(".")]
    if not 1 <= parts[1] <= 12:
        raise ValueError(f"invalid Home Assistant release month: {value!r}")
    return parts[0], parts[1], parts[2] if len(parts) == 3 else 0


def _diagnostics(payload: Any) -> dict[str, Any]:
    # HA wraps integration diagnostics in a metadata envelope. Also accept the
    # integration's data directly, so no manual editing of a download is needed.
    data = payload.get("data", payload) if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise ValueError("expected Breakage Radar diagnostics")
    for section in ("index", "settings", "scan", "summary", "cards"):
        if not isinstance(data.get(section), dict):
            raise ValueError(f"missing diagnostics section: {section}")
    for section, key in (
        ("settings", "ignored_domains"),
        ("summary", "not_analysed"),
        ("cards", "not_analysed"),
    ):
        if not isinstance(data[section].get(key), list) or not all(
            isinstance(name, str) and name for name in data[section][key]
        ):
            raise ValueError(f"missing diagnostics list: {section}.{key}")
    for section in ("summary", "cards"):
        count = data[section].get("installed")
        if type(count) is not int or count < 0:
            raise ValueError(f"invalid diagnostics count: {section}.installed")
        if len(data[section]["not_analysed"]) > count:
            raise ValueError(f"not_analysed exceeds installed count: {section}")
    installed = data["summary"].get("installed_versions")
    if installed is not None and (
        not isinstance(installed, dict) or not all(
            isinstance(name, str) and name and isinstance(version, str)
            for name, version in installed.items()
        )
    ):
        raise ValueError("invalid installed integration versions")
    if not isinstance(data.get("findings"), list):
        raise ValueError("missing diagnostics findings")
    if type(data.get("findings_truncated")) is not bool:
        raise ValueError("missing diagnostics findings_truncated flag")
    if type(data.get("total_findings")) is not int or data["total_findings"] < len(data["findings"]):
        raise ValueError("invalid diagnostics total_findings")
    for finding in data["findings"]:
        if not isinstance(finding, dict) or not all(
            isinstance(finding.get(key), str)
            for key in ("domain", "rule_id", "breaks_in", "file", "source")
        ):
            raise ValueError("invalid diagnostics finding")
        if not finding["domain"] or not finding["rule_id"]:
            raise ValueError("finding requires a component and rule identifier")
        if finding.get("kind", "integration") not in ("integration", "card"):
            raise ValueError("invalid diagnostics finding kind")
        if finding["source"] not in ("local", "index"):
            raise ValueError("invalid diagnostics finding source")
        if "line" in finding and (type(finding["line"]) is not int or finding["line"] < 0):
            raise ValueError("invalid diagnostics finding line")
    affected = data["summary"].get("affected", 0)
    affected_cards = data["cards"].get("affected", [])
    if type(affected) is not int or not 0 <= affected <= data["summary"]["installed"]:
        raise ValueError("invalid affected integration count")
    if not isinstance(affected_cards, list) or len(affected_cards) > data["cards"]["installed"] or not all(
        isinstance(name, str) and name for name in affected_cards
    ):
        raise ValueError("invalid affected cards")
    if data["total_findings"] == 0 and (affected or affected_cards):
        raise ValueError("affected components reported without findings")
    return data


def _identity(finding: dict[str, Any]) -> tuple[str, ...]:
    # Line numbers move when code is edited. A missing match is not proof of a
    # fix: scans, rules and index coverage may also have changed.
    try:
        deadline = str(release_key(finding["breaks_in"]))
    except ValueError:
        deadline = finding["breaks_in"]
    return (
        finding.get("kind", "integration"), finding["domain"],
        finding["rule_id"], deadline, finding["file"],
    )


def _unmatched(findings: list[dict], baseline: list[dict]) -> list[dict]:
    # Match unchanged locations first. Only then allow a match to have moved,
    # otherwise removing line 1 while retaining line 10 can report line 10 gone.
    exact = Counter((_identity(f), str(f.get("line", ""))) for f in baseline)
    pending = []
    for finding in findings:
        key = (_identity(finding), str(finding.get("line", "")))
        if exact[key]:
            exact[key] -= 1
        else:
            pending.append(finding)
    remaining = Counter()
    for (identity, _line), count in exact.items():
        remaining[identity] += count
    unmatched = []
    for finding in pending:
        identity = _identity(finding)
        if remaining[identity]:
            remaining[identity] -= 1
        else:
            unmatched.append(finding)
    return unmatched


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: Any) -> datetime:
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        raise ValueError("timezone missing")
    return timestamp


def _timestamp_gaps(value: Any, label: str, max_age_days: int | None) -> list[str]:
    gaps = []
    try:
        generated = _timestamp(value)
    except (TypeError, ValueError):
        gaps.append(f"The {label} timestamp is unknown or invalid; freshness cannot be checked.")
    else:
        age = _utcnow() - generated
        if age < -timedelta(minutes=5):
            gaps.append(f"The {label} timestamp is in the future; check the export and system clock.")
        elif max_age_days is not None and age / timedelta(days=1) > max_age_days:
            gaps.append(f"The {label} is older than {max_age_days} days; refresh before upgrading.")
    return gaps


def _coverage_gaps(
    data: dict[str, Any], max_index_age_days: int, max_scan_age_days: int,
    *, historical: bool = False,
) -> list[str]:
    # A baseline is intentionally historical. Age alone does not make its
    # observations incomplete; missing metadata and failed/partial scans still do.
    gaps = _timestamp_gaps(data["index"].get("generated_utc"), "index", None if historical else max_index_age_days)
    health = data.get("health")
    if not isinstance(health, dict) or health.get("index_update_success") is not True:
        gaps.append("The latest index update is unsuccessful or its health is unknown.")
    if isinstance(health, dict) and health.get("index_error"):
        gaps.append("The coordinator reports an index error; the export may contain retained results.")
    if isinstance(health, dict) and health.get("local_scan_in_progress"):
        gaps.append("A local scan was still in progress when diagnostics were exported.")
    if data["findings_truncated"] or data["total_findings"] != len(data["findings"]):
        gaps.append("The export omits findings; target counts are lower bounds.")
    for section, label in (("summary", "integrations"), ("cards", "cards")):
        if data[section]["not_analysed"]:
            gaps.append(f"Some installed {label} were not analysed.")
    if data["settings"]["ignored_domains"]:
        gaps.append("Ignored integrations or cards are excluded from the scan.")
    scan = data["scan"]
    if scan.get("enabled") is not True and data["summary"]["installed"]:
        gaps.append("Local scanning was not enabled; index results may not match installed versions.")
    counters = ["unparsed_files", "skipped_files", "skipped_minified_files"]
    if data["cards"]["installed"]:
        counters += ["card_unparsed_files", "card_skipped_files"]
    for key in counters:
        if type(scan.get(key)) is not int or scan[key] != 0:
            gaps.append(f"Scan coverage is incomplete or unknown: {key}.")
    coverage = data.get("coverage")
    for section, label in (("summary", "integrations"), ("cards", "cards")):
        if not data[section]["installed"]:
            continue
        group = coverage.get(label) if isinstance(coverage, dict) else None
        errors = health.get("scan_errors") if isinstance(health, dict) else None
        if not isinstance(errors, dict) or label not in errors or errors[label] is not None:
            gaps.append(f"The latest local scan health is failed or unknown for {label}.")
        components = group.get("components") if isinstance(group, dict) else None
        if not isinstance(components, dict) or len(components) != data[section]["installed"]:
            gaps.append(f"Per-component local scan coverage is unavailable or incomplete for {label}.")
            continue
        if type(group.get("rules_matchable")) is not int or group["rules_matchable"] <= 0:
            gaps.append(f"No local matchable rules are established for {label}.")
        gaps.extend(_timestamp_gaps(group.get("completed_utc"), f"{label} local scan", None if historical else max_scan_age_days))
        try:
            same_index = _timestamp(group.get("index_generated_utc")) == _timestamp(data["index"].get("generated_utc"))
        except (TypeError, ValueError):
            same_index = False
        if not same_index:
            gaps.append(f"The {label} local scan was not established against this index snapshot.")
        try:
            impossible_order = _timestamp(group.get("completed_utc")) - _timestamp(group.get("index_generated_utc")) < -timedelta(minutes=5)
        except (TypeError, ValueError):
            impossible_order = False
        if impossible_order:
            gaps.append(f"The {label} local scan predates its recorded index context; chronology is inconsistent.")
        if label == "integrations":
            installed = data["summary"].get("installed_versions")
            discovered = group.get("discovered_versions")
            if not isinstance(installed, dict) or set(installed) != set(components):
                gaps.append("Installed integration identities or versions are missing or inconsistent with local coverage.")
            elif not isinstance(discovered, dict) or discovered != installed:
                gaps.append("Discovered integration versions changed since the local scan or their scan-time versions are unknown.")
        known = {f["domain"] for f in data["findings"] if f.get("kind", "integration") == ("card" if label == "cards" else "integration")}
        if not known <= components.keys():
            gaps.append(f"Finding identities disagree with the local coverage for {label}.")
        for name, component in components.items():
            if not isinstance(component, dict) or component.get("status") not in ("clean", "affected"):
                gaps.append(f"Local scan did not establish coverage for {label}: {name}.")
                continue
            count = component.get("files_scanned")
            if type(count) is not int or count <= 0:
                gaps.append(f"No local source files were established as scanned for {label}: {name}.")
            for key in ("unparsed_files", "skipped_files", "unreadable_directories"):
                if type(component.get(key)) is not int or component[key] != 0:
                    gaps.append(f"Local coverage is incomplete or unknown for {label}: {name} ({key}).")
            if label == "cards" and (
                type(component.get("skipped_minified")) is not int or component["skipped_minified"] != 0
            ):
                gaps.append(f"Local minified-file coverage is incomplete or unknown for cards: {name}.")
    if any(f["source"] != "local" for f in data["findings"]):
        gaps.append("Index findings describe crawled code, not necessarily the installed version.")
    for finding in data["findings"]:
        try:
            release_key(finding["breaks_in"])
        except ValueError:
            gaps.append("Some findings have no supported release deadline.")
            break
    return gaps


def build_upgrade_report(
    payload: Any, target_version: str, *, previous: Any = None,
    max_index_age_days: int = 7, max_scan_age_days: int = 1,
) -> dict[str, Any]:
    """Partition the saved findings without changing sensor alert semantics."""
    target = release_key(target_version)
    if type(max_index_age_days) is not int or max_index_age_days < 0:
        raise ValueError("max_index_age_days must be a nonnegative integer")
    if type(max_scan_age_days) is not int or max_scan_age_days < 0:
        raise ValueError("max_scan_age_days must be a nonnegative integer")
    data = _diagnostics(payload)
    due, later, undated = [], [], []
    for finding in data["findings"]:
        try:
            deadline = release_key(finding["breaks_in"])
        except ValueError:
            undated.append(finding)
        else:
            (due if deadline <= target else later).append(finding)

    gaps = _coverage_gaps(data, max_index_age_days, max_scan_age_days)

    result = {
        "target_version": target_version,
        "status": "known_findings" if due else ("incomplete" if gaps else "no_known_findings"),
        "has_coverage_gaps": bool(gaps),
        "index": data["index"],
        "scan": data["scan"],
        "coverage": data.get("coverage"),
        "max_index_age_days": max_index_age_days,
        "max_scan_age_days": max_scan_age_days,
        "health": data.get("health"),
        "installed_versions": data["summary"].get("installed_versions"),
        "due_by_target": due,
        "after_target": later,
        "unknown_deadline": undated,
        "coverage_gaps": gaps,
        "not_analysed": {
            "integrations": data["summary"]["not_analysed"],
            "integration_reasons": data["summary"].get("not_analysed_reasons", {}),
            "cards": data["cards"]["not_analysed"],
            "card_reasons": data["cards"].get("not_analysed_reasons", {}),
            "ignored": data["settings"]["ignored_domains"],
        },
        "limitations": [
            "No known findings is not a guarantee of compatibility. Only known static rules were checked.",
            "This is a saved scan. Re-scan installed code before upgrading; check the index timestamp.",
            "Fixed integration releases are not established by these diagnostics.",
            "Older exports may lack per-component local coverage. Index-clean results do not prove installed code was scanned.",
        ],
    }
    if previous is not None:
        old = _diagnostics(previous)
        previous_gaps = _coverage_gaps(old, max_index_age_days, max_scan_age_days, historical=True)
        if previous_gaps:
            gaps.append("The previous export has coverage gaps; the comparison is incomplete.")
        result["changes"] = {
            "comparison_complete": not bool(gaps),
            "previous_index": old["index"],
            "newly_observed": _unmatched(data["findings"], old["findings"]),
            "no_longer_observed": _unmatched(old["findings"], data["findings"]),
            "previous_coverage_gaps": previous_gaps,
            "exports_truncated": (
                old["findings_truncated"] or data["findings_truncated"]
                or old["total_findings"] != len(old["findings"])
                or data["total_findings"] != len(data["findings"])
            ),
            "note": (
                "Observed changes are not confirmed fixes or regressions; coverage, rules and installed code may differ. "
                "Unmatched duplicate locations are representative, not proven added or removed lines."
            ),
        }
        result["has_coverage_gaps"] = bool(gaps)
        if gaps and not due:
            result["status"] = "incomplete"
    return result


def render_text(report: dict[str, Any]) -> str:
    lines = [f"Home Assistant {report['target_version']}: {report['status']}"]
    lines.append(f"Index generated: {report['index'].get('generated_utc') or 'unknown'}")
    for key, title in (
        ("due_by_target", "Due by target"),
        ("after_target", "After target"),
        ("unknown_deadline", "Unknown deadline"),
    ):
        lines.extend(["", f"{title}: {len(report[key])}"])
        for finding in report[key]:
            lines.append(
                f"  {finding['domain']} [{finding.get('kind', 'integration')}] "
                f"{finding['rule_id']} -> {finding['breaks_in'] or 'unknown'} "
                f"({finding['source']}, {finding['file']}:{finding.get('line', 0)})"
            )
            lines.append(
                f"    Installed: {finding.get('installed_version') or 'unknown'}; "
                f"scanned: {finding.get('scanned_version') or 'unknown'}; "
                f"repository: {finding.get('repository') or 'unknown'}"
            )
            for field in ("message", "learn_more"):
                if finding.get(field):
                    lines.append(f"    {finding[field]}")
    for name, values in report["not_analysed"].items():
        if values:
            lines.append(f"{name}: {json.dumps(values, ensure_ascii=True)}")
    for note in report["coverage_gaps"] + report["limitations"]:
        lines.append(f"- {note}")
    if "changes" in report:
        changes = report["changes"]
        lines.append(
            f"Changes: {len(changes['newly_observed'])} newly observed, "
            f"{len(changes['no_longer_observed'])} no longer observed."
        )
        lines.append(changes["note"])
        for gap in changes["previous_coverage_gaps"]:
            lines.append(f"Previous export: {gap}")
        if changes["exports_truncated"]:
            lines.append("Comparison incomplete: one or both exports omit findings.")
    # Source-controlled paths and messages must not inject terminal controls.
    # ASCII escapes also keep output usable on legacy Windows console encodings.
    return "\n".join(_terminal_text(line) for line in lines)


def _terminal_text(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)[1:-1]


def _load_diagnostics(path: Path) -> Any:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid_constant(value: str) -> Any:
        raise ValueError(f"invalid JSON constant: {value}")

    return json.loads(
        path.read_text(encoding="utf-8-sig"),
        object_pairs_hook=unique_object, parse_constant=invalid_constant,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("diagnostics", type=Path)
    parser.add_argument("--target-version", required=True, help="stable HA release, e.g. 2026.12")
    parser.add_argument("--previous", type=Path, help="earlier diagnostics export for observed changes")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--max-index-age-days", type=int, default=7, help="freshness limit (default: 7 days)")
    parser.add_argument("--max-scan-age-days", type=int, default=1, help="local scan freshness limit (default: 1 day)")
    args = parser.parse_args(argv)
    try:
        payload = _load_diagnostics(args.diagnostics)
        previous = _load_diagnostics(args.previous) if args.previous else None
        if args.previous and previous is None:
            raise ValueError("expected Breakage Radar diagnostics in the previous export")
        report = build_upgrade_report(
            payload, args.target_version, previous=previous,
            max_index_age_days=args.max_index_age_days,
            max_scan_age_days=args.max_scan_age_days,
        )
    except (OSError, ValueError, RecursionError) as err:
        print(_terminal_text(f"Could not build upgrade report: {err}"), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, ensure_ascii=True) if args.format == "json" else render_text(report))
    if report["has_coverage_gaps"]:
        return 2
    return 1 if report["due_by_target"] else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
