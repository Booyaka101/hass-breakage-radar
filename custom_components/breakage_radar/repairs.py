"""Repairs issue raised when installed custom integrations are going to break.

The issue is deliberately *not* fixable in place. Frenck, reviewing the legacy
device tracker deprecation (architecture discussion 1375):

    "Repairs must be user actionable, and in this case, they can't solve it."

The action a user *can* take is real though: open the upstream repository's
issue tracker, or replace the integration before the deadline. So the issue is
informational, links straight to the board, and clears itself the moment the
count returns to zero.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, ISSUE_ID

LEARN_MORE_URL = "https://booyaka101.github.io/hass-breakage-radar/"


#: Prefix for the per-integration issues raised once a deadline has passed.
BROKEN_ISSUE_PREFIX = "broken_now_"


@callback
def _async_sync_broken_issues(hass: HomeAssistant, report: dict) -> None:
    """One issue per integration whose removal release has already arrived.

    Unlike the year-ahead aggregate, these *are* individually actionable right
    now -- the integration is failing on this very system, so "update it, file
    upstream, or replace it today" is a real instruction. Anything that
    recovers (an update ships, the component is uninstalled) has its issue
    deleted on the next refresh.
    """
    broken: dict[str, str] = report.get("broken_now") or {}

    for domain, release in broken.items():
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"{BROKEN_ISSUE_PREFIX}{domain}",
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="integration_broken",
            translation_placeholders={
                "domain": domain,
                "release": str(release),
            },
            learn_more_url=LEARN_MORE_URL,
        )

    # Everything this report knows about that is *not* broken now.
    known = (
        set(report.get("affected_domains") or [])
        | set(report.get("clean_domains") or [])
        | set(report.get("not_in_index") or [])
    )
    for domain in known - set(broken):
        ir.async_delete_issue(hass, DOMAIN, f"{BROKEN_ISSUE_PREFIX}{domain}")

    # An uninstalled component vanishes from the report entirely; the registry
    # is the only place its stale issue can still be found.
    async_get = getattr(ir, "async_get", None)
    if async_get is None:
        return
    registry = async_get(hass)
    for issue_domain, issue_id in list(getattr(registry, "issues", {})):
        if (
            issue_domain == DOMAIN
            and issue_id.startswith(BROKEN_ISSUE_PREFIX)
            and issue_id[len(BROKEN_ISSUE_PREFIX):] not in broken
        ):
            ir.async_delete_issue(hass, DOMAIN, issue_id)


@callback
def async_sync_issue(hass: HomeAssistant, report: dict) -> None:
    """Create, update or clear the Breakage Radar repairs issues."""
    _async_sync_broken_issues(hass, report)

    affected = report.get("affected_domains") or []
    if not affected:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_ID)
        return

    by_release: dict[str, list[str]] = report.get("by_release") or {}
    earliest = report.get("earliest_release") or (
        min(by_release) if by_release else "a future release"
    )
    releases = ", ".join(
        f"{release} ({len(domains)})" for release, domains in by_release.items()
    )
    broken_count = report.get("broken_now_count") or 0

    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_ID,
        is_fixable=False,
        is_persistent=False,
        severity=(
            ir.IssueSeverity.ERROR if broken_count else ir.IssueSeverity.WARNING
        ),
        translation_key="integrations_affected",
        translation_placeholders={
            "count": str(len(affected)),
            "earliest": str(earliest),
            "integrations": ", ".join(sorted(affected)[:12])
            + (" and others" if len(affected) > 12 else ""),
            "releases": releases or "-",
        },
        learn_more_url=LEARN_MORE_URL,
    )
