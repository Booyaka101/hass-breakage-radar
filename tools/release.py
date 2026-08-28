"""Resolve the latest *released* Home Assistant core version.

Core's ``dev`` branch bumps to N+1 as soon as the N branch is cut, about two
weeks before N ships, so during that RC window dev is two releases ahead of
what anybody runs. Pending-ness has to be measured against the newest release
that actually shipped, not against dev -- otherwise every rule for the release
in RC drops out of the scan in exactly the week a user still has time to act
(#46).

PyPI answers that in one request: ``info.version`` of the ``homeassistant``
package is the latest stable release. The result is cached on disk with a
short TTL so repeated runs stay off the network. When the lookup fails, or the
caller is offline, the fallback is the issue's simpler heuristic: treat dev
minus one as not yet released. That keeps a just-shipped release on the board
for the rest of the month rather than hiding one that has not shipped, and
every consumer says so out loud instead of degrading silently.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.common import CACHE_DIR, LOGGER, http_get_json, read_json, write_json
from tools.rules_engine import VERSION_RE, is_future, normalise_version, parse_version

PYPI_URL = "https://pypi.org/pypi/homeassistant/json"
CACHE_FILE = CACHE_DIR / "latest_release.json"
CACHE_TTL_SECONDS = 6 * 3600

FALLBACK_SOURCE = "dev-minus-one"


@dataclass(frozen=True)
class ReleaseFloor:
    """The earliest release still treated as pending."""

    floor: str
    latest: str | None  # the released version this was resolved from, if known
    source: str  # "pypi" | "cache" | "dev-minus-one"


def _year_month(release: str) -> tuple[int, int]:
    key = parse_version(normalise_version(release))
    return key[0], key[1] if len(key) > 1 else 0


def previous_release(release: str) -> str:
    """The monthly release before ``release``: 2026.10 -> 2026.9, 2027.1 -> 2026.12."""
    year, month = _year_month(release)
    if month <= 1:
        return f"{year - 1}.12"
    return f"{year}.{month - 1}"


def next_release(release: str) -> str:
    """The monthly release after ``release``: 2026.8 -> 2026.9, 2026.12 -> 2027.1."""
    year, month = _year_month(release)
    if month >= 12:
        return f"{year + 1}.1"
    return f"{year}.{month + 1}"


def _released_version(version: Any, dev_version: str) -> str | None:
    """``version`` as a release label, if it could plausibly be one.

    Pre-release strings (``2026.9.0b3``) fail the plain calendar-version
    check, and anything at or past dev is garbage: dev is by definition
    unreleased.
    """
    if not isinstance(version, str) or not VERSION_RE.match(version):
        return None
    if not is_future(dev_version, version):
        return None
    return normalise_version(version)


def resolve_latest_release(
    dev_version: str,
    *,
    offline: bool = False,
    cache_path: Path | None = None,
    ttl: float = CACHE_TTL_SECONDS,
    now: float | None = None,
) -> ReleaseFloor:
    """The pending floor, from PyPI, the disk cache, or the fallback."""
    cache_path = CACHE_FILE if cache_path is None else cache_path
    now = time.time() if now is None else now

    cached = read_json(cache_path, default=None)
    if isinstance(cached, dict) and now - cached.get("fetched_at", 0) < ttl:
        latest = _released_version(cached.get("version"), dev_version)
        if latest:
            return ReleaseFloor(next_release(latest), latest, "cache")

    if not offline:
        try:
            info = http_get_json(PYPI_URL, timeout=10, max_attempts=2)
            version = info.get("info", {}).get("version")
        except Exception as err:
            LOGGER.warning("PyPI lookup failed: %s", err)
        else:
            latest = _released_version(version, dev_version)
            if latest:
                write_json(cache_path, {"version": version, "fetched_at": now})
                return ReleaseFloor(next_release(latest), latest, "pypi")
            LOGGER.warning(
                "PyPI reports %r, which is not a released calendar version", version
            )

    floor = previous_release(dev_version)
    LOGGER.warning(
        "latest released core version unknown; treating %s (dev minus one) as "
        "not yet shipped",
        floor,
    )
    return ReleaseFloor(floor, None, FALLBACK_SOURCE)


def floor_from_payload(payload: dict[str, Any]) -> tuple[str, str]:
    """``(floor, source)`` recorded in a rules payload, however old the file.

    A payload written before the floor existed falls back to dev minus one,
    and either way a degraded floor is announced rather than silent: it keeps
    an already-shipped release's rules listed for the rest of the month.
    """
    floor = payload.get("pending_floor")
    source = payload.get("pending_floor_source", "pypi") if floor else FALLBACK_SOURCE
    if not floor:
        floor = previous_release(payload.get("core_version", "2026.9"))
    if source == FALLBACK_SOURCE:
        LOGGER.warning(
            "latest released core version unknown; treating %s (dev minus one) as "
            "not yet shipped -- rules for an already-shipped release may still "
            "be listed",
            floor,
        )
    return floor, source
