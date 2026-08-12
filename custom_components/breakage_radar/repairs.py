"""Repairs issues for integrations that are going to break.

None of these are fixable in place, because the code lives in someone else's
repository. What the user can do is real though: update the integration, raise
it upstream, or replace it before the deadline.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, ISSUE_ID

LEARN_MORE_URL = "https://booyaka101.github.io/hass-breakage-radar/"

BROKEN_ISSUE_PREFIX = "broken_now_"
IMMINENT_ISSUE_PREFIX = "imminent_"
ALERT_PREFIXES = (BROKEN_ISSUE_PREFIX, IMMINENT_ISSUE_PREFIX)

#: Keeps the summary description readable when a lot is scheduled.
MAX_SCHEDULE_LINES = 8
MAX_DOMAINS_PER_LINE = 6


def format_schedule(schedule: list[dict], only: set[str] | None = None) -> str:
    """A dated timeline of what breaks when, one release per line.

    ``2027.5 - May 2027, about 9 months away: pycupra, some_tracker``
    """
    lines: list[str] = []
    hidden = 0
    for group in schedule:
        domains = group.get("domains") or []
        if only is not None:
            domains = [d for d in domains if d in only]
        if not domains:
            continue
        if len(lines) >= MAX_SCHEDULE_LINES:
            hidden += len(domains)
            continue
        shown = domains[:MAX_DOMAINS_PER_LINE]
        names = ", ".join(shown)
        if len(domains) > len(shown):
            names += f" and {len(domains) - len(shown)} more"
        lines.append(f"{group['release']} - {group['due']}: {names}")
    if hidden:
        lines.append(f"...and {hidden} more further out.")
    return "\n".join(lines)


@callback
def _async_sync_alert_issues(hass: HomeAssistant, report: dict) -> None:
    """One card per integration that needs attention now, or soon.

    Everything further out stays in the single summary issue, because Repairs
    has no snooze and a wall of undismissable year-ahead cards just trains
    people to ignore the panel.
    """
    broken: dict[str, str] = report.get("broken_now") or {}
    imminent: dict[str, dict] = report.get("imminent") or {}
    due_by_release = {
        group["release"]: group["due"] for group in report.get("schedule") or []
    }

    for domain, release in broken.items():
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"{BROKEN_ISSUE_PREFIX}{domain}",
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="integration_broken",
            translation_placeholders={"domain": domain, "release": str(release)},
            learn_more_url=LEARN_MORE_URL,
        )

    for domain, entry in imminent.items():
        release = str(entry.get("release", ""))
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"{IMMINENT_ISSUE_PREFIX}{domain}",
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="integration_imminent",
            translation_placeholders={
                "domain": domain,
                "release": release,
                "days": str(max(int(entry.get("days", 0)), 0)),
                "due": due_by_release.get(release, release),
            },
            learn_more_url=LEARN_MORE_URL,
        )

    # Anything that dropped out of a level, including an uninstalled component
    # that has vanished from the report, must lose its card.
    async_get = getattr(ir, "async_get", None)
    if async_get is None:
        return
    registry = async_get(hass)
    for issue_domain, issue_id in list(getattr(registry, "issues", {})):
        if issue_domain != DOMAIN:
            continue
        if issue_id.startswith(BROKEN_ISSUE_PREFIX):
            if issue_id[len(BROKEN_ISSUE_PREFIX):] not in broken:
                ir.async_delete_issue(hass, DOMAIN, issue_id)
        elif issue_id.startswith(IMMINENT_ISSUE_PREFIX):
            if issue_id[len(IMMINENT_ISSUE_PREFIX):] not in imminent:
                ir.async_delete_issue(hass, DOMAIN, issue_id)


@callback
def async_sync_issue(hass: HomeAssistant, report: dict) -> None:
    """Create, update or clear the Breakage Radar repairs issues."""
    _async_sync_alert_issues(hass, report)

    summarised = report.get("summarised_domains") or []
    if not summarised:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_ID)
        return

    schedule = report.get("schedule") or []
    remaining = [
        group
        for group in schedule
        if any(domain in set(summarised) for domain in group.get("domains") or [])
    ]
    first = remaining[0] if remaining else None

    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_ID,
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="integrations_affected",
        translation_placeholders={
            "count": str(len(summarised)),
            "earliest": first["release"] if first else "a future release",
            "earliest_due": first["due"] if first else "",
            "schedule": format_schedule(schedule, only=set(summarised)),
            "window": str(report.get("alert_window_days") or 0),
        },
        learn_more_url=LEARN_MORE_URL,
    )
