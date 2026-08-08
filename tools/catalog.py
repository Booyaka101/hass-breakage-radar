#!/usr/bin/env python3
"""Fetch the catalogue of every HACS custom integration.

Primary source: ``https://data-v2.hacs.xyz/integration/data.json`` -- an object
keyed by numeric GitHub repository id, each value carrying ``full_name``,
``domain``, ``last_version``, ``stargazers_count``, ``open_issues`` and
``last_updated``.

Fallback: ``https://raw.githubusercontent.com/hacs/default/master/integration``
-- a plain JSON array of ``owner/repo`` slugs. The fallback has no domain or
version, so the scanner discovers those from the repository itself.

Writes ``data/catalog.json``.

Usage::

    python tools/catalog.py [--output data/catalog.json] [--force-fallback]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.common import (  # noqa: E402
    DATA_DIR,
    LOGGER,
    http_get_json,
    setup_logging,
    utc_now_iso,
    write_json,
)

PRIMARY_URL = "https://data-v2.hacs.xyz/integration/data.json"
FALLBACK_URL = "https://raw.githubusercontent.com/hacs/default/master/integration"


def normalise_primary(payload: Any) -> list[dict[str, Any]]:
    """Normalise the data-v2 object into ``[{full_name, domain, last_version, ...}]``."""
    if not isinstance(payload, dict):
        raise ValueError(
            f"expected a JSON object keyed by repo id, got {type(payload).__name__}"
        )

    entries: list[dict[str, Any]] = []
    for repo_id, value in payload.items():
        if not isinstance(value, dict):
            continue
        full_name = value.get("full_name")
        if not full_name or "/" not in full_name:
            continue
        entries.append(
            {
                "full_name": full_name,
                "domain": value.get("domain") or "",
                "last_version": value.get("last_version") or "",
                "stargazers_count": int(value.get("stargazers_count") or 0),
                "open_issues": int(value.get("open_issues") or 0),
                "last_updated": value.get("last_updated") or "",
                "repo_id": str(repo_id),
            }
        )
    return entries


def normalise_fallback(payload: Any) -> list[dict[str, Any]]:
    """Normalise the hacs/default array of ``owner/repo`` slugs."""
    if not isinstance(payload, list):
        raise ValueError(
            f"expected a JSON array of slugs, got {type(payload).__name__}"
        )
    entries: list[dict[str, Any]] = []
    for slug in payload:
        if not isinstance(slug, str) or "/" not in slug:
            continue
        entries.append(
            {
                "full_name": slug,
                "domain": "",
                "last_version": "",
                "stargazers_count": 0,
                "open_issues": 0,
                "last_updated": "",
                "repo_id": "",
            }
        )
    return entries


def fetch_catalog(
    *,
    primary_url: str = PRIMARY_URL,
    fallback_url: str = FALLBACK_URL,
    force_fallback: bool = False,
) -> tuple[list[dict[str, Any]], str]:
    """Return ``(entries, source_url)``, falling back on any primary failure."""
    if not force_fallback:
        try:
            payload = http_get_json(primary_url, timeout=120)
            entries = normalise_primary(payload)
            if entries:
                return entries, primary_url
            LOGGER.warning("%s returned zero usable entries", primary_url)
        except Exception as err:  # network, JSON, or shape problem
            LOGGER.warning("primary catalogue %s failed: %s", primary_url, err)

    LOGGER.info("falling back to %s", fallback_url)
    payload = http_get_json(fallback_url, timeout=120)
    entries = normalise_fallback(payload)
    if not entries:
        raise RuntimeError(
            f"both {primary_url} and {fallback_url} returned an unusable catalogue"
        )
    return entries, fallback_url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--output", type=Path, default=DATA_DIR / "catalog.json")
    parser.add_argument(
        "--force-fallback",
        action="store_true",
        help="skip data-v2.hacs.xyz and use the hacs/default list",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    try:
        entries, source = fetch_catalog(force_fallback=args.force_fallback)
    except Exception as err:
        LOGGER.error("could not build a catalogue: %s", err)
        return 1

    entries.sort(key=lambda e: e["full_name"].lower())
    with_domain = sum(1 for e in entries if e["domain"])
    with_version = sum(1 for e in entries if e["last_version"])

    write_json(
        args.output,
        {
            "schema": 1,
            "generated_utc": utc_now_iso(),
            "source": source,
            "counts": {
                "total": len(entries),
                "with_domain": with_domain,
                "with_last_version": with_version,
            },
            "integrations": entries,
        },
    )
    LOGGER.info(
        "wrote %s: %d integrations from %s (%d with a domain, %d with a version)",
        args.output,
        len(entries),
        source,
        with_domain,
        with_version,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
