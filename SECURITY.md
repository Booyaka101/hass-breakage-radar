# Security Policy

## Supported versions

The latest published version is the only one that gets fixes.

## Reporting a vulnerability

Please **don't** open a public issue for a security problem.

Use GitHub's [private vulnerability reporting](https://github.com/Booyaka101/hass-breakage-radar/security/advisories/new) instead. Expect a first response within a week.

Please include what you found, how to reproduce it, and what an attacker gets out of it.

## What this touches

Reads public Home Assistant sources and integration repos over HTTPS. It never touches your Home Assistant instance or its config.

- **It never touches your Home Assistant instance.** It reads public sources over HTTPS: Home Assistant core, the developer blog and the integration repositories you name.

## Scope

In scope: anything that leaks a credential, reads data belonging to someone else, or lets untrusted input reach code execution.

Out of scope: findings that require an attacker to already control the machine it runs on.
