#!/usr/bin/env python3
"""Scan HACS custom integrations for use of soon-to-be-removed Home Assistant APIs.

For each repository in ``data/catalog.json`` this downloads
``https://codeload.github.com/{full_name}/tar.gz/refs/tags/{last_version}``
(falling back to ``v``-prefixed tags, then ``refs/heads/main``, then
``refs/heads/master``), reads every ``custom_components/**/*.py`` member
**straight out of the tarball** without extracting anything, and runs the rule
matchers over it.

Designed to be interrupted. A slice always ends with ``state/crawl.json`` and
``data/findings.json`` written, so the next run resumes where this one stopped:

* tarball 404 on every ref  -> ``status: unreachable``, recorded, continue
* no ``custom_components/`` -> ``status: no_custom_components``, recorded, continue
* ``SyntaxError`` in a file -> counted, that file skipped, the run continues
* HTTP 429 / rate limit     -> stop the slice cleanly with state committed

Usage::

    python tools/scan.py --limit 25
    python tools/scan.py --limit 400 --only dave-code-ruiz/elkbledom
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import tarfile
import time
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.common import (  # noqa: E402
    DATA_DIR,
    LOGGER,
    STATE_DIR,
    NotFound,
    RateLimited,
    http_get,
    read_json,
    setup_logging,
    utc_now_iso,
    write_json,
)
from tools.rules_engine import (  # noqa: E402
    ENGINE_VERSION,
    Finding,
    Rule,
    ScanStats,
    load_rules,
    match_source,
    matchable_rules,
)

CODELOAD = "https://codeload.github.com/{full_name}/tar.gz/{ref}"

#: Refuse to buffer a source tarball larger than this. Some HACS repos vendor
#: firmware blobs; the Python we care about is always tiny.
MAX_TARBALL_BYTES = 80 * 1024 * 1024

#: Skip vendored third-party code shipped inside a custom component.
VENDOR_MARKERS = ("/site-packages/", "/node_modules/", "/.venv/", "/vendor/")


def candidate_refs(last_version: str) -> list[str]:
    """Git refs to try, most specific first."""
    refs: list[str] = []
    version = (last_version or "").strip()
    if version:
        refs.append(f"refs/tags/{version}")
        if not version.startswith("v"):
            refs.append(f"refs/tags/v{version}")
        else:
            refs.append(f"refs/tags/{version.lstrip('v')}")
    refs.extend(["refs/heads/main", "refs/heads/master"])
    seen: set[str] = set()
    return [r for r in refs if not (r in seen or seen.add(r))]


def fetch_tarball(full_name: str, last_version: str) -> tuple[bytes, str]:
    """Download the first ref that exists. Raises :class:`NotFound` if none do."""
    last_error: Exception | None = None
    for ref in candidate_refs(last_version):
        url = CODELOAD.format(full_name=full_name, ref=ref)
        try:
            body = http_get(url, timeout=180)
        except NotFound as err:
            last_error = err
            continue
        if len(body) > MAX_TARBALL_BYTES:
            raise RuntimeError(
                f"{full_name}@{ref} is {len(body) // 1024 // 1024} MB; skipping"
            )
        return body, ref
    raise NotFound(f"no downloadable ref for {full_name}: {last_error}")


def iter_component_python(body: bytes) -> Iterator[tuple[str, bytes]]:
    """Yield ``(custom_components/... path, source)`` from a repo tarball."""
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
        for member in archive:
            if not member.isfile() or member.size > 4 * 1024 * 1024:
                continue
            _, _, relative = member.name.partition("/")
            if not relative.endswith(".py"):
                continue
            index = relative.find("custom_components/")
            if index == -1:
                continue
            path = relative[index:]
            if any(marker in "/" + path for marker in VENDOR_MARKERS):
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            yield path, handle.read()


def iter_manifest_domains(body: bytes) -> list[str]:
    """Domains declared by ``custom_components/*/manifest.json`` in the tarball."""
    domains: list[str] = []
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
        for member in archive:
            if not member.isfile() or member.size > 512 * 1024:
                continue
            _, _, relative = member.name.partition("/")
            index = relative.find("custom_components/")
            if index == -1 or not relative.endswith("/manifest.json"):
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            try:
                payload = json.loads(handle.read().decode("utf-8", "replace"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            domain = payload.get("domain")
            if isinstance(domain, str) and domain:
                domains.append(domain)
            else:
                parts = PurePosixPath(relative[index:]).parts
                if len(parts) >= 2:
                    domains.append(parts[1])
    return sorted(set(domains))


def findings_hash(findings: list[dict[str, Any]]) -> str:
    blob = json.dumps(findings, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def rules_hash(rules: list[Rule]) -> str:
    """Identity of "what a scan would produce": rules *and* engine semantics."""
    blob = json.dumps(
        [
            {"engine": ENGINE_VERSION},
            *[
                {"id": r.id, "breaks_in": r.breaks_in, "match": r.match}
                for r in sorted(rules, key=lambda r: r.id)
            ],
        ],
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def scan_repo(
    entry: dict[str, Any], rules: list[Rule]
) -> tuple[dict[str, Any], list[Finding]]:
    """Scan one repository. Returns ``(record, findings)``.

    :class:`RateLimited` propagates -- the caller ends the slice. Every other
    failure is captured in ``record["status"]``.
    """
    full_name = entry["full_name"]
    record: dict[str, Any] = {
        "domain": entry.get("domain") or "",
        "version": entry.get("last_version") or "",
        "ref": "",
        "status": "scanned",
        "scanned_utc": utc_now_iso(),
        "files_scanned": 0,
        "syntax_errors": 0,
        "stargazers_count": entry.get("stargazers_count", 0),
        "findings": [],
    }

    try:
        body, ref = fetch_tarball(full_name, entry.get("last_version", ""))
    except NotFound as err:
        record["status"] = "unreachable"
        record["error"] = str(err)[:200]
        return record, []
    except RateLimited:
        raise
    except Exception as err:
        record["status"] = "error"
        record["error"] = f"{type(err).__name__}: {err}"[:200]
        return record, []

    record["ref"] = ref

    try:
        domains = iter_manifest_domains(body)
        stats = ScanStats()
        findings: list[Finding] = []
        for path, source in iter_component_python(body):
            findings.extend(match_source(path, source, rules, stats))
    except tarfile.TarError as err:
        record["status"] = "error"
        record["error"] = f"bad tarball: {err}"[:200]
        return record, []

    if domains:
        # Prefer the domain the repository itself declares.
        record["domain"] = domains[0] if len(domains) == 1 else record["domain"]
        record["domains"] = domains
        if not record["domain"]:
            record["domain"] = domains[0]
    elif stats.files_scanned == 0:
        record["status"] = "no_custom_components"

    record["files_scanned"] = stats.files_scanned
    record["syntax_errors"] = len(stats.syntax_errors)
    record["findings"] = [f.to_dict() for f in findings]
    return record, findings


def select_slice(
    catalog: list[dict[str, Any]],
    state: dict[str, Any],
    *,
    limit: int,
    current_rules_hash: str,
    force: bool,
) -> list[dict[str, Any]]:
    """Least-recently-scanned first, skipping repos with nothing new."""
    pending: list[dict[str, Any]] = []
    for entry in catalog:
        previous = state.get(entry["full_name"])
        if previous and not force:
            same_version = previous.get("last_version_scanned", "") == (
                entry.get("last_version") or ""
            )
            same_rules = previous.get("rules_hash") == current_rules_hash
            if same_version and same_rules:
                continue
        pending.append(entry)

    def sort_key(entry: dict[str, Any]) -> tuple[int, str, str]:
        previous = state.get(entry["full_name"])
        if not previous:
            return (0, "", entry["full_name"])
        return (1, previous.get("last_scanned_utc", ""), entry["full_name"])

    pending.sort(key=sort_key)
    return pending


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--limit", type=int, default=400, help="repos per run")
    parser.add_argument("--catalog", type=Path, default=DATA_DIR / "catalog.json")
    parser.add_argument("--rules", type=Path, default=DATA_DIR / "rules.json")
    parser.add_argument("--findings", type=Path, default=DATA_DIR / "findings.json")
    parser.add_argument("--state", type=Path, default=STATE_DIR / "crawl.json")
    parser.add_argument(
        "--only", action="append", default=None, help="scan only these owner/repo"
    )
    parser.add_argument(
        "--force", action="store_true", help="rescan even if nothing changed"
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        help="seconds to pause between repositories",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    rules_payload = read_json(args.rules, default=None)
    if rules_payload is None:
        LOGGER.error("%s not found -- run tools/extract_rules.py first", args.rules)
        return 2
    current_version = rules_payload.get("core_version", "2026.9")
    all_rules = load_rules(rules_payload.get("rules", []))
    active = matchable_rules(all_rules, current_version=current_version)
    if not active:
        LOGGER.error("no matchable future rules; refusing to scan")
        return 2
    rhash = rules_hash(active)
    LOGGER.info(
        "%d matchable rules (core %s, rules_hash %s)",
        len(active),
        current_version,
        rhash,
    )

    catalog_payload = read_json(args.catalog, default=None)
    if catalog_payload is None:
        LOGGER.error("%s not found -- run tools/catalog.py first", args.catalog)
        return 2
    catalog = catalog_payload.get("integrations", [])
    if not catalog:
        LOGGER.error("%s contains no integrations", args.catalog)
        return 2

    if args.only:
        wanted = set(args.only)
        catalog = [e for e in catalog if e["full_name"] in wanted]
        missing = wanted - {e["full_name"] for e in catalog}
        for name in sorted(missing):
            LOGGER.warning("%s is not in the catalogue; skipping", name)
        if not catalog:
            LOGGER.error("none of --only matched the catalogue")
            return 2

    state: dict[str, Any] = read_json(args.state, default={}) or {}
    findings_doc = read_json(args.findings, default=None) or {
        "schema": 1,
        "repos": {},
    }
    repos: dict[str, Any] = findings_doc.setdefault("repos", {})

    pending = select_slice(
        catalog,
        state,
        limit=args.limit,
        current_rules_hash=rhash,
        force=args.force or bool(args.only),
    )
    todo = pending[: args.limit]
    LOGGER.info(
        "%d/%d repositories need a scan; this slice takes %d",
        len(pending),
        len(catalog),
        len(todo),
    )
    if not todo:
        LOGGER.info("nothing to do -- every repository is up to date")
        return 0

    def checkpoint() -> None:
        """Persist progress mid-slice.

        A long crawl that only writes at the end is indistinguishable from a
        hung one, and loses everything if the runner is killed.
        """
        findings_doc["schema"] = 1
        findings_doc["updated_utc"] = utc_now_iso()
        findings_doc["rules_hash"] = rhash
        findings_doc["core_version"] = current_version
        write_json(args.findings, findings_doc)
        write_json(args.state, state)

    started = time.time()
    counters = {
        "scanned": 0,
        "unreachable": 0,
        "no_custom_components": 0,
        "error": 0,
        "with_findings": 0,
        "findings": 0,
    }
    stopped_early = False

    for index, entry in enumerate(todo, start=1):
        full_name = entry["full_name"]
        try:
            record, findings = scan_repo(entry, active)
        except RateLimited as err:
            LOGGER.warning("rate limited (%s) -- ending slice cleanly at %d/%d", err, index - 1, len(todo))
            stopped_early = True
            break
        except KeyboardInterrupt:
            LOGGER.warning("interrupted -- committing state")
            stopped_early = True
            break

        counters[record["status"]] = counters.get(record["status"], 0) + 1
        if findings:
            counters["with_findings"] += 1
            counters["findings"] += len(findings)

        repos[full_name] = record
        state[full_name] = {
            "last_version_scanned": entry.get("last_version") or "",
            "last_scanned_release_tag": record["ref"],
            "last_scanned_utc": record["scanned_utc"],
            "status": record["status"],
            "findings_hash": findings_hash(record["findings"]),
            "rules_hash": rhash,
        }

        LOGGER.info(
            "[%d/%d] %-52s %-20s %2d finding(s)%s",
            index,
            len(todo),
            full_name[:52],
            record["status"],
            len(record["findings"]),
            f"  {record['ref']}" if record["ref"] else "",
        )
        if index % 25 == 0:
            checkpoint()
        if args.sleep:
            time.sleep(args.sleep)

    checkpoint()

    LOGGER.info(
        "slice done in %.0fs: %s | state has %d repos, findings file has %d repos%s",
        time.time() - started,
        ", ".join(f"{k}={v}" for k, v in counters.items() if v),
        len(state),
        len(repos),
        " (ENDED EARLY)" if stopped_early else "",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
