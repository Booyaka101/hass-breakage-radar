"""Shared HTTP + filesystem helpers for the Breakage Radar crawler.

Standard library only.  Everything that touches the network goes through
:func:`http_get`, which centralises the retry / backoff / rate-limit policy so a
crawl slice can always end cleanly with its state committed.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("breakage_radar.tools")

def _resolve_root() -> Path:
    """Where ``data/``, ``docs/`` and ``state/`` live.

    Running from a checkout (the normal case, and what the GitHub Actions
    workflow does) this is the repository root. Installed as a package the
    sibling of ``tools/`` is ``site-packages``, which is the wrong place to
    write a crawl into -- so fall back to the current working directory.
    ``BREAKAGE_RADAR_ROOT`` overrides both.
    """
    override = os.environ.get("BREAKAGE_RADAR_ROOT")
    if override:
        return Path(override).resolve()
    candidate = Path(__file__).resolve().parent.parent
    if (candidate / "custom_components" / "breakage_radar").is_dir():
        return candidate
    return Path.cwd().resolve()


REPO_ROOT = _resolve_root()
DATA_DIR = REPO_ROOT / "data"
DOCS_DIR = REPO_ROOT / "docs"
STATE_DIR = REPO_ROOT / "state"
CACHE_DIR = REPO_ROOT / ".cache"

#: Repositories their owners asked to have removed (issue #65). Hand-maintained
#: like ``data/manual_rules.json``: one record per withdrawal, with the reason
#: and a link to the request. A missing file excludes nothing.
EXCLUDED_PATH = DATA_DIR / "excluded.json"

USER_AGENT = (
    "hass-breakage-radar/1.0.0 (+https://github.com/Booyaka101/hass-breakage-radar)"
)

DEFAULT_TIMEOUT = 60
MAX_ATTEMPTS = 4


class RateLimited(RuntimeError):
    """Raised when the remote host keeps answering 429/403-rate-limited.

    The crawler treats this as "end this slice cleanly", never as a crash.
    """


class NotFound(RuntimeError):
    """A 404 from the remote host. Callers usually fall back to another URL."""


def _sleep(seconds: float) -> None:
    # Split out so tests can monkeypatch it and run instantly.
    time.sleep(seconds)


def http_get(
    url: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    max_attempts: int = MAX_ATTEMPTS,
    accept: str | None = None,
) -> bytes:
    """GET ``url`` and return the raw body.

    * 404 -> :class:`NotFound` immediately (no point retrying a missing tag).
    * 429, 403-with-rate-limit-headers, 5xx and transient socket errors ->
      exponential backoff (1s, 2s, 4s, ...).  If every attempt fails with a
      rate limit, :class:`RateLimited` is raised so the caller can stop the
      slice instead of hammering GitHub.
    """
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
    if accept:
        headers["Accept"] = accept

    last_error: Exception | None = None
    rate_limited = False

    for attempt in range(max_attempts):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return body
        except urllib.error.HTTPError as err:
            # The error *is* the response, and nothing else closes it. Left to
            # the collector it prints "I/O operation on closed file" from a
            # finaliser, at whatever moment happens to be least convenient.
            err.close()
            if err.code == 404:
                raise NotFound(f"404 for {url}") from err
            retryable = err.code in (403, 408, 429, 500, 502, 503, 504)
            if err.code in (403, 429):
                rate_limited = True
            last_error = err
            if not retryable:
                raise RuntimeError(f"HTTP {err.code} for {url}") from err
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            last_error = err

        if attempt < max_attempts - 1:
            delay = 2.0**attempt
            LOGGER.warning(
                "GET %s failed (%s); retrying in %.0fs", url, last_error, delay
            )
            _sleep(delay)

    if rate_limited:
        raise RateLimited(f"rate limited fetching {url}: {last_error}")
    raise RuntimeError(f"GET {url} failed after {max_attempts} attempts: {last_error}")


def http_get_json(url: str, **kwargs: Any) -> Any:
    """GET ``url`` and parse the body as JSON, with a readable error on garbage."""
    body = http_get(url, accept="application/json", **kwargs)
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise RuntimeError(f"{url} did not return valid JSON: {err}") from err


def download_to(url: str, destination: Path, **kwargs: Any) -> Path:
    """Download ``url`` to ``destination`` atomically. Returns the destination."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    body = http_get(url, **kwargs)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.write_bytes(body)
    os.replace(temporary, destination)
    return destination


def read_json(path: Path, default: Any = None) -> Any:
    """Read JSON from ``path``; return ``default`` if missing or corrupt."""
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, UnicodeDecodeError) as err:
        LOGGER.warning("%s is not valid JSON (%s); treating as empty", path, err)
        return default


def write_json(path: Path, payload: Any, *, indent: int = 2) -> Path:
    """Write ``payload`` as UTF-8 JSON with a trailing newline, atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=indent, ensure_ascii=False, sort_keys=False)
        handle.write("\n")
    os.replace(temporary, path)
    return path


def utc_now_iso() -> str:
    """Current UTC time as ``2026-08-08T12:34:56Z``."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )


def load_excluded(path: Path | None = None) -> list[dict[str, Any]]:
    """Read ``data/excluded.json`` into a list of withdrawal records.

    Accept the UTF-8 BOM some editors write. A present but invalid file must
    stop the crawl, never silently turn owner withdrawals into an empty list.
    A missing file remains optional for standalone uses of the tools.
    """
    path = path or EXCLUDED_PATH
    try:
        with path.open(encoding="utf-8-sig") as handle:
            doc = json.load(handle)
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, UnicodeDecodeError) as err:
        raise ValueError(f"invalid withdrawal list {path}: {err}") from err
    if not isinstance(doc, dict) or not isinstance(doc.get("repositories"), list):
        raise ValueError(f"invalid withdrawal list {path}: expected repositories array")
    for record in doc["repositories"]:
        if not isinstance(record, dict) or not isinstance(record.get("full_name"), str):
            raise ValueError(f"invalid withdrawal record in {path}: expected full_name")
        if len(record["full_name"].split("/")) != 2 or not all(record["full_name"].split("/")):
            raise ValueError(f"invalid withdrawal record in {path}: expected owner/repo")
        former_names = record.get("former_names", [])
        if not isinstance(former_names, list) or any(
            not isinstance(name, str) or len(name.split("/")) != 2 or not all(name.split("/"))
            for name in former_names
        ):
            raise ValueError(f"invalid withdrawal record in {path}: expected former_names array of slugs")
        if "domain" in record and not isinstance(record["domain"], str):
            raise ValueError(f"invalid withdrawal record in {path}: expected domain string")
        if "repo_id" in record and (
            not isinstance(record["repo_id"], (str, int)) or isinstance(record["repo_id"], bool)
        ):
            raise ValueError(f"invalid withdrawal record in {path}: expected repository ID")
    return doc["repositories"]


class Exclusion:
    """Match catalog entries and crawl records against the withdrawal list.

    A repository is matched by any name it has carried -- a rename leaves the
    old name in ``findings.json`` and ``state/crawl.json``, as
    ``Kaluzaburza/Hoymiles_HIT_xxL_G3_ModBus`` did -- by its numeric GitHub
    id, and by its integration domain. The domain is what the published index
    keys on, so it is also what a rename cannot shake off.
    """

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self._names: dict[str, dict[str, Any]] = {}
        self._repo_ids: dict[str, dict[str, Any]] = {}
        self._domains: dict[str, dict[str, Any]] = {}
        for record in records:
            for name in [record.get("full_name", ""), *(record.get("former_names") or [])]:
                if isinstance(name, str) and name:
                    self._names[name] = record
            repo_id = record.get("repo_id")
            if repo_id:
                self._repo_ids[str(repo_id)] = record
            domain = record.get("domain")
            if isinstance(domain, str) and domain:
                self._domains[domain] = record

    def names(self) -> set[str]:
        """Every name (current and former) across the records."""
        return set(self._names)

    def record_for(
        self, full_name: str, entry: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """The withdrawal record covering this repository, or ``None``.

        ``entry`` is a catalog entry or a crawl record; either may carry the
        ``repo_id`` or ``domain`` a rename left behind.
        """
        record = self._names.get(full_name)
        if record is not None or entry is None:
            return record
        repo_id = entry.get("repo_id")
        if repo_id:
            record = self._repo_ids.get(str(repo_id))
        if record is not None:
            return record
        domains = entry.get("domains")
        if not isinstance(domains, list):
            domains = [entry.get("domain")] if entry.get("domain") else []
        for domain in domains:
            if isinstance(domain, str) and domain in self._domains:
                return self._domains[domain]
        return None
