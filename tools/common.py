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
