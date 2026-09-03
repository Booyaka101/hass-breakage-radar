# For integration authors

*Your integration is on the board. Here is what that means, how to check it
yourself, and how to get it removed — including if we are wrong.*

The rest of this project's documentation is written for Home Assistant *users*
asking "which of my integrations break?". This page is for the other side: you
maintain one of the integrations, and you would like the finding gone.

---

## What a listing actually claims

Breakage Radar found, by static analysis of your published source, a call or
definition that Home Assistant has **announced it will remove in a specific
release**. That is the entire claim. It is not a claim that your integration is
broken today, badly written, or unmaintained — only that this line stops working
on a date that is already on the calendar.

A finding always carries five things:

| Field | Meaning |
|---|---|
| `rule_id` | which removal this is |
| `breaks_in` | the Home Assistant release that removes it |
| `file` / `line` | exactly where, in the ref that was scanned |
| `confidence` | `high` — the match is unambiguous; `medium` — see below |
| `source` (on the rule) | the blog post or core source line announcing the removal |

**`confidence: medium` means the import graph could not prove the receiver's
type.** For example `registry.async_get_device(...)`: if `registry` is only
known at runtime, no static check can be sure it is the device registry rather
than something of yours that shares a method name. Those rules opt into
firing anyway, because the alternative is missing real cases — but they are the
findings most worth disputing.

**A short name is only ever flagged inside the class it belongs to.** A rule for
`battery_level` would be useless on its own, since half the ecosystem has a
property by that name. What the rule actually says is "`battery_level` defined
on a class deriving from `StateVacuumEntity`", and the finding names both. If
your class does not derive from the base class in the message, that is a bug in
the rule and worth reporting.

---

## Check it yourself first

The crawler only visits repositories listed in the HACS catalogue, at their
last released tag. So it cannot answer questions about your working branch, a
fork, or a private integration. This does:

```bash
git clone https://github.com/Booyaka101/hass-breakage-radar
cd hass-breakage-radar

python tools/check_local.py /path/to/your-integration
```

It reads the published index for its rules and runs the same matchers over the
checkout you point it at. Real output against this repository's own test
fixture:

```
INFO breakage_radar.tools: 41 matchable rule(s) from https://booyaka101.github.io/hass-breakage-radar/index.json
INFO breakage_radar.tools: scanned 1 file(s); 0 unparseable, 0 skipped

custom_components/fixture_tracker/device_tracker.py:12
    breaks in Home Assistant 2027.5 (high confidence, rule legacy-device-tracker-platform)
    Implements the legacy (non-config-entry) device tracker platform API...
    https://developers.home-assistant.io/blog/2026/04/20/legacy-device-tracker-deprecation/

1 finding(s).
```

and on a clean checkout:

```
OK - no scheduled removals found in this checkout.
```

Exit codes make it usable as a release gate in your own CI:

| Code | Meaning |
|---|---|
| `0` | nothing found, or nothing that `--fail-on` says is worth failing over |
| `1` | at least one finding, and `--fail-on` counts it |
| `2` | **could not check** — nothing scannable, unreachable index, or no rules left |

Exit 2 is deliberately not exit 0. A check that could not run has proved
nothing, and must never be mistaken for a clean result. A card repository
whose only file is a minified bundle lands here for the same reason.

A checkout with `custom_components/` is read as an integration: its Python,
plus any frontend module shipped alongside it. A checkout without one is read
as a Lovelace card repository, and its JavaScript is scanned wherever it lives.
You do not have to say which.

Useful flags:

```bash
python tools/check_local.py . --ha-version 2027.5   # only removals still ahead of 2027.5
python tools/check_local.py . --rules data/rules.json   # offline, no index fetch
python tools/check_local.py . --fail-on imminent    # only fail on what is close
```

> **Use Python 3.12 or newer, ideally 3.14.** The checker parses your code with
> the interpreter it runs on. On an older interpreter, a file using newer syntax
> (PEP 695 `type X = ...`, PEP 758 `except A, B:`) fails to parse and is
> reported as unparseable rather than checked. The count of unparseable files is
> always printed — if it is not zero, upgrade before trusting the result.

---

## In your CI, as a GitHub Action

Same scanner, same rules, wired into pull requests so the finding lands on the
line rather than in someone else's Home Assistant log.

```yaml
name: Breakage Radar
on: [pull_request]

jobs:
  removals:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: Booyaka101/hass-breakage-radar@v1.9.1
```

That is the whole thing. It works unchanged on a card repository.

By default it **annotates and does not fail**. A removal scheduled for 2027.8
turning an unrelated pull request red is how a check gets deleted from a
workflow file, and then it is not there to warn you about the one landing next
month either. When you want a gate, ask for one:

```yaml
      - uses: Booyaka101/hass-breakage-radar@v1.9.1
        with:
          fail-on: imminent    # already released, or within window-days
          window-days: "90"
```

| Input | Default | What it does |
|---|---|---|
| `path` | `.` | checkout root, or a `custom_components` directory |
| `fail-on` | `never` | `never`, `imminent`, or `any` |
| `window-days` | `90` | how far ahead `imminent` reaches |
| `rules` | `pinned` | `pinned` uses the rules committed at the tag you pinned, with no network call. `index` fetches the published index instead |
| `ha-version` | *(empty)* | only report removals still ahead of this release |
| `python-version` | `3.13` | the interpreter that parses your source |

Findings come out as annotations on the exact line, **and** as a job summary
table. Both, because GitHub only displays 10 annotations per level per step and
50 per job — with the summary, thirty findings read as thirty rather than as
ten.

Pin an exact tag rather than a moving major. `data/rules.json` is rewritten by
the daily crawl, so a floating ref would quietly change what your CI checks
against; a tag is a rule set you chose.

---

## After you ship a fix

Nothing needs to be filed. The crawler re-scans a repository when its **released
version changes** (it reads `last_version` from the HACS catalogue and fetches
that tag) or when the rule set changes. So:

1. Fix the code and **cut a release** — the crawler follows tags, not the
   default branch, so an unreleased commit on `main` will not clear the listing.
2. Wait for the daily crawl. Coverage rotates least-recently-scanned first, so
   allow a day or two.
3. The finding disappears from `index.json` and the board on the next build.

Users running the Breakage Radar integration see it clear sooner than that: as
of v1.1.0 it scans the code actually installed on their system, so once they
update your integration, the local scan overrides the published index
immediately.

---

## If you think the finding is wrong

**Please report it.** A false positive is worse than a missing rule: it taxes
every user of every integration the rule fires on, and it costs you support
questions you should never have received.

Open an [issue](https://github.com/Booyaka101/hass-breakage-radar/issues) with
the `rule_id`, the `file` and `line` from the finding, and one sentence on why
the code is fine. That is enough to reproduce it — the finding names an exact
line in a public repository.

This is not a formality. Two rules were written from correct specifications and
were still wrong on real repositories; both were found this way and both changed
the engine:

* `entity_registry.async_generate_entity_id` is removed in 2027.2, but
  `entity.async_generate_entity_id` is fine — and they share a name. A
  name-only matcher flagged an integration that imports the healthy one.
  Matchers now resolve imports before firing, and can be pinned to a module.
* `@deprecated_hass_argument` deprecates the leading `hass` **argument**, not
  the function. One integration called `async_extract_entity_ids(call)` and
  `async_extract_entity_ids(hass, call)` on consecutive lines — one healthy, one
  not. A dedicated matcher now tells them apart, and one rule's hit count
  dropped from 1 to 0 as a result.

Every rule's measured hit rate across the catalogue is published in
`index.json`, precisely so a rule firing on an implausible fraction of
integrations is visible rather than quietly taxing everyone.

---

## What this project will not do

* **It will not open pull requests against your repository.** No automated
  fixes, no drive-by PRs, no bot comments on your issue tracker.
* **It will not tell users your integration is broken.** The wording is always
  which release removes the API and when. Users are pointed at your repository
  to ask, not away from it.
* **It does not rank or score maintainers.** There is no leaderboard, and the
  board sorts by release deadline, not by author.

The point is that the removal notice reaches somebody before upgrade day.
Home Assistant logs these warnings at `LOG` level for custom integrations and
deliberately keeps them out of Repairs, so in practice nobody sees them —
neither your users nor, if you are not reading the developer blog, you.

---

## Adding a rule yourself

If you know of an announced removal that is not covered, see
[Contributing a rule](../README.md#contributing-a-rule). Rules need a `source`
URL that states the removal release, and a matcher tight enough that it does not
fire across half the catalogue.
