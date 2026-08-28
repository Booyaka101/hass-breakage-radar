#!/usr/bin/env python3
"""Extract breakage rules straight out of the Home Assistant core source tree.

Downloads ``https://codeload.github.com/home-assistant/core/tar.gz/refs/heads/dev``,
AST-walks every ``homeassistant/**/*.py`` *inside the tarball* (nothing is
extracted to disk) and collects every call that passes a **string literal** to
``breaks_in_ha_version``.

The ground truth for the signature is ``homeassistant/helpers/frame.py``::

    def report_usage(
        what: str,
        *,
        breaks_in_ha_version: str | None = None,
        core_behavior: ReportBehavior = ReportBehavior.ERROR,
        core_integration_behavior: ReportBehavior = ReportBehavior.LOG,
        custom_integration_behavior: ReportBehavior = ReportBehavior.LOG,
        ...
    ) -> None:

``custom_integration_behavior`` defaults to ``LOG`` -- a custom integration that
trips one of these gets a line in ``home-assistant.log`` and nothing else. That
is precisely why this project exists.

Writes ``data/rules.json``.

Usage::

    python tools/extract_rules.py [--ref dev] [--offline] [--output data/rules.json]
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import logging
import re
import sys
import tarfile
from pathlib import Path
from typing import Any, Iterator

if __package__ in (None, ""):  # allow `python tools/extract_rules.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.common import (  # noqa: E402
    CACHE_DIR,
    DATA_DIR,
    LOGGER,
    download_to,
    setup_logging,
    utc_now_iso,
    write_json,
)
from tools.release import resolve_latest_release  # noqa: E402
from tools.rules_engine import (  # noqa: E402
    VERSION_RE,
    Rule,
    is_future,
    is_pending,
    normalise_version,
)

CORE_TARBALL = "https://codeload.github.com/home-assistant/core/tar.gz/refs/heads/{ref}"

#: Callables whose ``breaks_in_ha_version`` describes a *developer API* change.
API_DEPRECATION_CALLS = frozenset(
    {
        "report_usage",
        "report",  # the pre-2025 name, still present in older branches
        "deprecated_function",
        "deprecated_class",
        "deprecated_substitute",
        "deprecated_hass_argument",
    }
)

#: Callables whose ``breaks_in_ha_version`` describes a *user configuration*
#: repair issue. They are recorded for the board but are never matchable in
#: third-party source, because the trigger is runtime config, not code.
ISSUE_CALLS = frozenset(
    {
        "async_create_issue",
        "create_issue",
        "DeprecatedInfo",
        "EntityDomainReplacementStrategy",
    }
)

#: Core's *other* removal mechanism. A module declares
#: ``_DEPRECATED_<Name> = DeprecatedAlias(replacement, "new.path", "2027.6")``
#: and hands its ``__getattr__`` to ``check_if_deprecated_constant``, so the
#: warning fires on the import rather than on a call. No ``breaks_in_ha_version``
#: keyword is involved anywhere, which is why the ``report_usage`` pass above
#: has never seen any of it. Found by the #25 log audit.
DEPRECATED_CONSTANT_CALLS = frozenset(
    {"DeprecatedAlias", "DeprecatedConstant", "DeprecatedConstantEnum"}
)

_DEPRECATED_PREFIX = "_DEPRECATED_"

#: Minimum length for an auto-derived symbol to be trusted as a matcher.
#: Short names like ``async_listen`` collide with everybody's own helpers.
#: See LESSONS 2026-07-27: a rule written from a spec is a hypothesis.
MIN_AUTO_SYMBOL_LEN = 18

#: Names that clear the length gate but are still too common to match on.
AUTO_SYMBOL_DENYLIST = frozenset(
    {
        "async_added_to_hass",
        "async_will_remove_from_hass",
        "async_update_ha_state",
        "async_write_ha_state",
    }
)

# `calls foo`, ``calls `foo` ``, `calls module.foo,`
_RE_CALLS = re.compile(r"\bcalls\s+`?([A-Za-z_][\w.]*)`?")
# `doesn't specify unit_class when calling async_import_statistics`
_RE_MISSING_KWARG = re.compile(
    r"doesn't specify\s+`?(\w+)`?\s+when calling\s+`?([A-Za-z_][\w.]*)`?"
)
# `calls async_handle_source_entity_changes with add_helper_config_entry_to_device`
_RE_CALL_WITH = re.compile(
    r"\bcalls\s+`?([A-Za-z_][\w.]*)`?\s+with\s+`?(\w+)`?"
)


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "rule"


def _tail(dotted: str) -> str:
    return dotted.rsplit(".", 1)[-1]


def _trusted(symbol: str) -> bool:
    return len(symbol) >= MIN_AUTO_SYMBOL_LEN and symbol not in AUTO_SYMBOL_DENYLIST


def _message_for(callee: str, symbol: str, release: str) -> str:
    """A readable message for helpers that carry no prose of their own."""
    if callee == "deprecated_hass_argument":
        return (
            f"passes `hass` to `{symbol}`, where the leading `hass` argument is "
            f"ignored and is removed in Home Assistant {release}. Drop the "
            f"argument from the call."
        )
    if callee == "deprecated_class":
        return f"uses `{symbol}`, which is deprecated and removed in Home Assistant {release}."
    return f"uses `{symbol}`, deprecated and removed in Home Assistant {release}."


def _call_matcher(symbol: str, module: str) -> dict[str, Any]:
    matcher: dict[str, Any] = {"type": "call", "names": [symbol]}
    if module:
        matcher["modules"] = [module]
    return matcher


def _literal(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _what_text(node: ast.Call) -> str:
    """Best-effort text of the first positional argument, f-strings included."""
    if not node.args:
        return ""
    first = node.args[0]
    literal = _literal(first)
    if literal is not None:
        return literal
    if isinstance(first, ast.JoinedStr):
        # Render `{expr}` placeholders as a readable marker.
        parts: list[str] = []
        for value in first.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                parts.append("{...}")
        return "".join(parts)
    try:
        return ast.unparse(first)
    except Exception:  # pragma: no cover - unparse is total in 3.12
        return ""


def _enclosing_name(chain: list[str]) -> str:
    return ".".join(chain)


def module_of(path: str) -> str:
    """``homeassistant/helpers/entity_registry.py`` -> the importable module."""
    stem = path[:-3] if path.endswith(".py") else path
    if stem.endswith("/__init__"):
        stem = stem[: -len("/__init__")]
    return stem.replace("/", ".")


def derive_matcher(
    callee: str, what: str, enclosing: str, path: str = ""
) -> dict[str, Any] | None:
    """Turn a human-readable deprecation message into a machine matcher.

    Returns ``None`` when nothing specific enough can be derived -- the rule is
    then published for information but never claims a repo is affected.

    Auto-derived ``call`` matchers are pinned to the module the deprecated
    function actually lives in. Without that, a rule for
    ``entity_registry.async_generate_entity_id`` fires on every call to the
    entirely healthy ``entity.async_generate_entity_id``, which is a real false
    positive measured on 0xAlon/dolphin during the first crawl slice.
    """
    module = module_of(path) if path else ""
    if callee == "deprecated_hass_argument":
        # Only the leading `hass` argument is deprecated, not the function.
        symbol = _tail(enclosing)
        if symbol and _trusted(symbol):
            matcher = _call_matcher(symbol, module)
            matcher["type"] = "call_hass_argument"
            return matcher
        return None

    if callee == "deprecated_function":
        # The *decorated* function is the deprecated one; the argument is the
        # replacement. `enclosing` is e.g. "FlowHandler.show_advanced_options".
        symbol = _tail(enclosing)
        if symbol and _trusted(symbol):
            return _call_matcher(symbol, module)
        return None

    if callee == "deprecated_class":
        symbol = _tail(enclosing)
        if symbol and _trusted(symbol):
            # A deprecated class shows up in third-party code either as a base
            # class or as a constructor call.
            return {"type": "classbase", "bases": [symbol]}
        return None

    if callee not in ("report_usage", "report"):
        return None

    match = _RE_MISSING_KWARG.search(what)
    if match:
        kwarg, target = match.group(1), _tail(match.group(2))
        if _trusted(target):
            matcher = _call_matcher(target, module)
            matcher["type"] = "call_missing_kwarg"
            matcher["kwarg"] = kwarg
            return matcher
        return None

    match = _RE_CALL_WITH.search(what)
    if match:
        target, kwarg = _tail(match.group(1)), match.group(2)
        if _trusted(target):
            matcher = _call_matcher(target, module)
            matcher["type"] = "call_kwarg"
            matcher["kwargs"] = [kwarg]
            return matcher
        return None

    match = _RE_CALLS.search(what)
    if match:
        target = _tail(match.group(1))
        if _trusted(target):
            return _call_matcher(target, module)
        return None

    return None


def _kind_for(callee: str, matcher: dict[str, Any] | None) -> str:
    if callee in ISSUE_CALLS:
        return "issue"
    if matcher is None:
        return "prose"
    return {
        "classbase": "classbase",
        "call": "call",
        "call_kwarg": "call",
        "call_missing_kwarg": "call",
        "call_hass_argument": "call",
        "attr": "attr",
        "attr_access": "attr",
        "moduledef": "moduledef",
    }.get(matcher["type"], "call")


def _symbol_for(callee: str, what: str, enclosing: str, matcher) -> str:
    if callee in ("deprecated_function", "deprecated_class", "deprecated_hass_argument"):
        return enclosing or what
    if matcher:
        names = matcher.get("names") or matcher.get("bases") or []
        if names:
            base = names[0]
            kwargs = matcher.get("kwargs") or (
                [matcher["kwarg"]] if matcher.get("kwarg") else []
            )
            if matcher["type"] == "call_kwarg" and kwargs:
                return f"{base}({kwargs[0]}=...)"
            if matcher["type"] == "call_missing_kwarg" and kwargs:
                return f"{base}(missing {kwargs[0]})"
            if matcher["type"] == "call_hass_argument":
                return f"{base}(hass, ...)"
            return base
    return (what[:80] + "...") if len(what) > 80 else what


def iter_core_python(tarball: Path) -> Iterator[tuple[str, bytes]]:
    """Yield ``(path_relative_to_repo_root, source_bytes)`` for core .py files."""
    with tarfile.open(tarball, "r:gz") as archive:
        for member in archive:
            if not member.isfile():
                continue
            _, _, relative = member.name.partition("/")
            if not relative.startswith("homeassistant/") or not relative.endswith(".py"):
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            yield relative, handle.read()


def core_version(tarball: Path) -> str:
    """Read MAJOR/MINOR_VERSION out of ``homeassistant/const.py``."""
    with tarfile.open(tarball, "r:gz") as archive:
        for member in archive:
            if not member.isfile():
                continue
            _, _, relative = member.name.partition("/")
            if relative != "homeassistant/const.py":
                continue
            handle = archive.extractfile(member)
            if handle is None:
                break
            text = handle.read().decode("utf-8", "replace")
            major = re.search(r"^MAJOR_VERSION:?[^=]*=\s*(\d+)", text, re.M)
            minor = re.search(r"^MINOR_VERSION:?[^=]*=\s*(\d+)", text, re.M)
            if major and minor:
                return f"{major.group(1)}.{minor.group(1)}"
            break
    raise RuntimeError("could not determine core version from homeassistant/const.py")


def _parent_map(tree: ast.Module) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _parse_if_interesting(
    path: str, source: bytes, marker: bytes, unparsed: list[str] | None
) -> ast.Module | None:
    """Parse a core file that contains ``marker``, or record why not.

    Home Assistant's ``dev`` branch tracks the newest CPython syntax, so an
    older interpreter cannot parse every core file (2026.9 dev uses PEP 758
    unparenthesized ``except A, B:``, which needs Python 3.14). Rather than
    pretend, every unparseable file is recorded and reported in ``rules.json``.
    Both extraction passes look at the same files, so a file that fails for
    both is recorded once.
    """
    if marker not in source:
        return None
    try:
        return ast.parse(source, filename=path)
    except (SyntaxError, ValueError) as err:
        LOGGER.warning("skipping %s: %s", path, err)
        entry = f"{path}: {err}"
        if unparsed is not None and entry not in unparsed:
            unparsed.append(entry)
        return None


def extract_from_source(
    path: str, source: bytes, unparsed: list[str] | None = None
) -> Iterator[dict[str, Any]]:
    """Yield raw call-site records from one core file."""
    tree = _parse_if_interesting(path, source, b"breaks_in_ha_version", unparsed)
    if tree is None:
        return

    parents = _parent_map(tree)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        callee = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if callee not in API_DEPRECATION_CALLS and callee not in ISSUE_CALLS:
            continue
        version: str | None = None
        for keyword in node.keywords:
            if keyword.arg == "breaks_in_ha_version":
                version = _literal(keyword.value)
        if not version:
            continue

        chain: list[str] = []
        current: ast.AST | None = parents.get(node)
        while current is not None:
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                chain.append(current.name)
            current = parents.get(current)
        chain.reverse()

        yield {
            "callee": callee,
            "version": version,
            "what": _what_text(node),
            "enclosing": _enclosing_name(chain),
            "path": path,
            "line": node.lineno,
        }


def extract_deprecated_constants(
    path: str, source: bytes, unparsed: list[str] | None = None
) -> Iterator[dict[str, Any]]:
    """Yield one record per ``_DEPRECATED_X = DeprecatedAlias(...)`` declaration.

    The release is whichever string argument looks like a release label, not a
    fixed position: ``DeprecatedConstantEnum`` takes two arguments and
    ``DeprecatedAlias`` three, and the replacement path is a string too.
    """
    tree = _parse_if_interesting(
        path, source, _DEPRECATED_PREFIX.encode(), unparsed
    )
    if tree is None:
        return

    module = module_of(path)
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        target = next(
            (
                t.id
                for t in node.targets
                if isinstance(t, ast.Name) and t.id.startswith(_DEPRECATED_PREFIX)
            ),
            None,
        )
        if target is None:
            continue
        func = node.value.func
        callee = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if callee not in DEPRECATED_CONSTANT_CALLS:
            continue
        literals = [
            arg.value
            for arg in node.value.args
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
        ]
        version = next((v for v in reversed(literals) if VERSION_RE.match(v)), None)
        if not version:
            LOGGER.debug("%s:%d: %s carries no release label", path, node.lineno, target)
            continue
        replacement = next((v for v in literals if v != version), "")
        yield {
            "callee": callee,
            "version": version,
            "symbol": target[len(_DEPRECATED_PREFIX) :],
            "module": module,
            "replacement": replacement,
            "what": "",
            "enclosing": "",
            "path": path,
            "line": node.lineno,
        }


def _import_rule(record: dict[str, Any], release: str) -> dict[str, Any]:
    """The rule for one deprecated import, as ``build_rules`` wants it."""
    symbol = record["symbol"]
    module = record["module"]
    replacement = record["replacement"]
    tail = module.rsplit(".", 1)[-1]
    advice = (
        f" Import it from {replacement.rsplit('.', 1)[0]} instead."
        if replacement
        else ""
    )
    return {
        "id": f"core-import-{_slug(tail)}-{_slug(symbol)}"[:90],
        "symbol": symbol,
        "message": (
            f"{symbol} imported from {module} is deprecated and is removed in "
            f"Home Assistant {release}.{advice}"
        ),
        # A named import from an exact module: there is no receiver to infer
        # and nothing to confuse it with, so the length gate that protects
        # call matchers does not apply here.
        "confidence": "high",
        "match": {
            "type": "import_from",
            "modules": [module],
            "names": [symbol],
        },
        "replacement": replacement,
    }


def build_rules(records: list[dict[str, Any]], pending_floor: str) -> list[dict[str, Any]]:
    """Collapse raw call sites into deduplicated, published rules."""
    by_id: dict[str, dict[str, Any]] = {}

    for record in sorted(records, key=lambda r: (r["path"], r["line"])):
        callee = record["callee"]
        version = record["version"]
        if not VERSION_RE.match(version):
            LOGGER.debug("skipping non-release version %r", version)
            continue

        release = normalise_version(version)
        imported = _import_rule(record, release) if callee in DEPRECATED_CONSTANT_CALLS else None

        if imported:
            matcher = imported["match"]
            symbol = imported["symbol"]
            kind = "import"
            rule_id = imported["id"]
        else:
            matcher = (
                derive_matcher(
                    callee, record["what"], record["enclosing"], record["path"]
                )
                if callee in API_DEPRECATION_CALLS
                else None
            )
            symbol = _symbol_for(callee, record["what"], record["enclosing"], matcher)
            kind = _kind_for(callee, matcher)
            if callee in ISSUE_CALLS:
                rule_id = (
                    f"core-issue-{_slug(record['enclosing'] or record['path'])}-{release}"
                )
            else:
                rule_id = f"core-{kind}-{_slug(symbol)}"
            rule_id = rule_id[:90]

        existing = by_id.get(rule_id)
        if existing:
            existing["occurrences"] += 1
            # Keep the earliest (lowest) removal release -- that is the deadline.
            if is_future(existing["breaks_in"], release):
                existing["breaks_in"] = release
            continue

        rule = Rule(
            id=rule_id,
            kind=kind,
            symbol=symbol,
            message=(
                imported["message"]
                if imported
                else record["what"] or _message_for(callee, symbol, release)
            ),
            breaks_in=release,
            source=f"homeassistant/{record['path'].split('homeassistant/', 1)[-1]}:{record['line']}"
            if record["path"].startswith("homeassistant/")
            else f"{record['path']}:{record['line']}",
            origin="core-ast",
            confidence=imported["confidence"] if imported else "medium",
            match=matcher,
            replacement=imported["replacement"] if imported else None,
        )
        payload = rule.to_dict()
        payload["expired"] = not is_pending(release, pending_floor)
        payload["occurrences"] = 1
        payload["source_url"] = (
            f"https://github.com/home-assistant/core/blob/dev/{record['path']}"
            f"#L{record['line']}"
        )
        by_id[rule_id] = payload

    return sorted(by_id.values(), key=lambda r: (r["breaks_in"], r["id"]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--ref", default="dev", help="core git ref (default: dev)")
    parser.add_argument(
        "--tarball",
        type=Path,
        default=None,
        help="use this local core tarball instead of downloading",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="fail instead of downloading if the cached tarball is missing",
    )
    parser.add_argument("--output", type=Path, default=DATA_DIR / "rules.json")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    if args.tarball:
        tarball = args.tarball
        if not tarball.exists():
            LOGGER.error("tarball %s does not exist", tarball)
            return 2
    else:
        tarball = CACHE_DIR / f"core-{args.ref}.tar.gz"
        if not tarball.exists():
            if args.offline:
                LOGGER.error("offline mode and no cached tarball at %s", tarball)
                return 2
            url = CORE_TARBALL.format(ref=args.ref)
            LOGGER.info("downloading %s", url)
            try:
                download_to(url, tarball, timeout=300)
            except Exception as err:
                LOGGER.error("could not download core source: %s", err)
                return 1
        else:
            LOGGER.info("using cached %s", tarball)

    digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
    try:
        current = core_version(tarball)
    except Exception as err:
        LOGGER.error("%s", err)
        return 1
    LOGGER.info("core version in tarball: %s (sha256 %s)", current, digest[:12])

    # dev runs ahead of what anybody has installed -- by two releases during
    # an RC window -- so pending-ness is measured against the newest release
    # that actually shipped, not against dev (#46).
    latest = resolve_latest_release(current, offline=args.offline)
    LOGGER.info(
        "latest released core: %s (%s); rules are pending from %s",
        latest.latest or "unknown",
        latest.source,
        latest.floor,
    )

    records: list[dict[str, Any]] = []
    unparsed: list[str] = []
    files = 0
    imports = 0
    for path, source in iter_core_python(tarball):
        files += 1
        records.extend(extract_from_source(path, source, unparsed))
        # Core announces removals two ways. Reading only report_usage() missed
        # the DeprecatedAlias class entirely until the #25 log audit found a
        # real integration warned about one.
        found = list(extract_deprecated_constants(path, source, unparsed))
        imports += len(found)
        records.extend(found)
    LOGGER.info(
        "scanned %d core files, %d deprecation call sites, %d deprecated import(s)",
        files,
        len(records) - imports,
        imports,
    )
    if unparsed:
        LOGGER.warning(
            "%d core file(s) could not be parsed by Python %d.%d - rules defined in "
            "them are missing. Run this tool on the newest CPython.",
            len(unparsed),
            sys.version_info.major,
            sys.version_info.minor,
        )

    rules = build_rules(records, latest.floor)
    future = [r for r in rules if not r["expired"]]
    matchable = [r for r in future if r["matchable"]]

    payload = {
        "schema": 1,
        "generated_utc": utc_now_iso(),
        "core_ref": args.ref,
        "core_version": current,
        "latest_release": latest.latest,
        "pending_floor": latest.floor,
        "pending_floor_source": latest.source,
        "core_tarball_sha256": digest,
        "core_source": CORE_TARBALL.format(ref=args.ref),
        "extractor_python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "counts": {
            "total": len(rules),
            "future": len(future),
            "matchable_future": len(matchable),
            "core_files_scanned": files,
            "core_files_unparsed": len(unparsed),
        },
        "unparsed_core_files": unparsed,
        "rules": rules,
    }
    write_json(args.output, payload)

    LOGGER.info(
        "wrote %s: %d rules (%d future, %d matchable)",
        args.output,
        len(rules),
        len(future),
        len(matchable),
    )
    for rule in matchable:
        LOGGER.info("  %-10s %-58s %s", rule["breaks_in"], rule["symbol"], rule["id"])

    if not any(VERSION_RE.match(r["breaks_in"]) for r in rules):
        LOGGER.error("no rule carries a parseable breaks_in version")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
