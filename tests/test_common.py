"""HTTP retry / backoff / rate-limit behaviour and safe JSON I/O."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from tools import common
from tools.common import NotFound, RateLimited, http_get, read_json, write_json


class _Response(io.BytesIO):
    def __init__(self, body: bytes, headers: dict | None = None):
        super().__init__(body)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://x.invalid", code, "boom", {}, None)


def test_404_raises_not_found_without_retrying(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(request.full_url)
        raise _http_error(404)

    monkeypatch.setattr(common.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(NotFound):
        http_get("https://x.invalid/thing")
    assert len(calls) == 1, "a missing tag must not be retried"


def test_429_backs_off_then_raises_rate_limited(monkeypatch):
    delays: list[float] = []
    attempts = []

    def fake_urlopen(request, timeout=None):
        attempts.append(1)
        raise _http_error(429)

    monkeypatch.setattr(common.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(common, "_sleep", delays.append)

    with pytest.raises(RateLimited):
        http_get("https://x.invalid/thing", max_attempts=4)
    assert len(attempts) == 4
    assert delays == [1.0, 2.0, 4.0], "backoff must be exponential"


def test_transient_failure_then_success(monkeypatch):
    state = {"n": 0}

    def fake_urlopen(request, timeout=None):
        state["n"] += 1
        if state["n"] < 3:
            raise _http_error(503)
        return _Response(b"ok")

    monkeypatch.setattr(common.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(common, "_sleep", lambda _: None)
    assert http_get("https://x.invalid/thing") == b"ok"
    assert state["n"] == 3


def test_non_retryable_status_fails_immediately(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(1)
        raise _http_error(451)

    monkeypatch.setattr(common.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="HTTP 451"):
        http_get("https://x.invalid/thing")
    assert len(calls) == 1


def test_gzip_bodies_are_decompressed(monkeypatch):
    import gzip

    body = gzip.compress(b'{"hello": "world"}')

    monkeypatch.setattr(
        common.urllib.request,
        "urlopen",
        lambda request, timeout=None: _Response(body, {"Content-Encoding": "gzip"}),
    )
    assert common.http_get_json("https://x.invalid/j") == {"hello": "world"}


def test_invalid_json_gives_a_readable_error(monkeypatch):
    monkeypatch.setattr(
        common.urllib.request,
        "urlopen",
        lambda request, timeout=None: _Response(b"<html>nope</html>"),
    )
    with pytest.raises(RuntimeError, match="did not return valid JSON"):
        common.http_get_json("https://x.invalid/j")


def test_read_json_tolerates_missing_and_corrupt_files(tmp_path):
    assert read_json(tmp_path / "nope.json", default={"a": 1}) == {"a": 1}
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{oops", encoding="utf-8")
    assert read_json(corrupt, default=[]) == []


def test_write_json_is_atomic_and_round_trips(tmp_path):
    target = tmp_path / "nested" / "out.json"
    write_json(target, {"b": 2, "a": 1})
    assert json.loads(target.read_text(encoding="utf-8")) == {"b": 2, "a": 1}
    assert not list(tmp_path.glob("**/*.part")), "temporary file must be cleaned up"
    assert target.read_text(encoding="utf-8").endswith("\n")
