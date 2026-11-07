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
        if not isinstance(data[section].get(key), list):
            raise ValueError(f"missing diagnostics list: {section}.{key}")
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
    return data


def _identity(finding: dict[str, Any]) -> tuple[str, ...]:
    # Line numbers move when code is edited. A missing match is not proof of a
    # fix: scans, rules and index coverage may also have changed.
    return tuple(
        str(finding.get(key, ""))
        for key in ("kind", "domain", "rule_id", "breaks_in", "file")
    )


def build_upgrade_report(
    payload: Any, target_version: str, *, previous: Any = None
) -> dict[str, Any]:
    """Partition the saved findings without changing sensor alert semantics."""
    target = release_key(target_version)
    data = _diagnostics(payload)
    due, later, undated = [], [], []
    for finding in data["findings"]:
        try:
            deadline = release_key(finding["breaks_in"])
        except ValueError:
            undated.append(finding)
        else:
            (due if deadline <= target else later).append(finding)

    gaps = []
    if not data["index"].get("generated_utc"):
        gaps.append("The index timestamp is unknown; freshness cannot be checked.")
    if data["findings_truncated"] or data["total_findings"] != len(data["findings"]):
        gaps.append("The export omits findings; target counts are lower bounds.")
    for section, label in (("summary", "integrations"), ("cards", "cards")):
        if data[section]["not_analysed"]:
            gaps.append(f"Some installed {label} were not analysed.")
    if data["settings"]["ignored_domains"]:
        gaps.append("Ignored integrations or cards are excluded from the scan.")
    scan = data["scan"]
    if scan.get("enabled") is not True:
        gaps.append("Local scanning was not enabled; index results may not match installed versions.")
    for key in ("unparsed_files", "skipped_files", "skipped_minified_files"):
        if type(scan.get(key)) is not int or scan[key] != 0:
            gaps.append(f"Scan coverage is incomplete or unknown: {key}.")
    if undated:
        gaps.append("Some findings have no supported release deadline.")
    if any(f["source"] != "local" for f in data["findings"]):
        gaps.append("Index findings describe crawled code, not necessarily the installed version.")

    result = {
        "target_version": target_version,
        "status": "known_findings" if due else ("incomplete" if gaps else "no_known_findings"),
        "has_coverage_gaps": bool(gaps),
        "index": data["index"],
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
            "Clean components may rely on index data; per-component clean-scan provenance is not exported.",
        ],
    }
    if previous is not None:
        old = _diagnostics(previous)
        old_ids = {_identity(f) for f in old["findings"]}
        new_ids = {_identity(f) for f in data["findings"]}
        result["changes"] = {
            "newly_observed": [f for f in data["findings"] if _identity(f) not in old_ids],
            "no_longer_observed": [f for f in old["findings"] if _identity(f) not in new_ids],
            "exports_truncated": (
                old["findings_truncated"] or data["findings_truncated"]
                or old["total_findings"] != len(old["findings"])
                or data["total_findings"] != len(data["findings"])
            ),
            "note": "Observed changes are not confirmed fixes or regressions; coverage, rules and installed code may differ.",
        }
    return result


def render_text(report: dict[str, Any]) -> str:
    lines = [f"Home Assistant {report['target_version']}: {report['status']}"]
    lines.append(f"Index generated: {report['index'].get('generated_utc') or 'unknown'}")
    for key, title in (
        ("due_by_target", "Due by target"),
        ("after_target", "After target"),
        ("unknown_deadline", "Unknown deadline"),
    ):
        lines.append(f"\n{title}: {len(report[key])}")
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
        if changes["exports_truncated"]:
            lines.append("Comparison incomplete: one or both exports omit findings.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("diagnostics", type=Path)
    parser.add_argument("--target-version", required=True, help="stable HA release, e.g. 2026.12")
    parser.add_argument("--previous", type=Path, help="earlier diagnostics export for observed changes")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.diagnostics.read_text(encoding="utf-8-sig"))
        previous = json.loads(args.previous.read_text(encoding="utf-8-sig")) if args.previous else None
        report = build_upgrade_report(payload, args.target_version, previous=previous)
    except (OSError, ValueError) as err:
        print(f"Could not build upgrade report: {err}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, ensure_ascii=True) if args.format == "json" else render_text(report))
    if report["has_coverage_gaps"]:
        return 2
    return 1 if report["due_by_target"] else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
