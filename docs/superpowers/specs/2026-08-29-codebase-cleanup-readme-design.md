# TelegramWall Codebase Cleanup and README Design

**Date:** 2026-08-29  
**Status:** proposed for implementation  
**Scope:** behavior-preserving refactor, documentation refresh, and GitHub Actions hardening

## Goal

Make TelegramWall easier to understand and maintain without changing user-visible
behavior, subscription rules, delivery ordering, schedules, database semantics, or
deployment topology. The repository documentation must describe the code that is
actually deployed today rather than the earlier Render-based setup.

## Non-goals

- No new bot features, commands, tariffs, or messages.
- No database schema or data migration changes.
- No change to polling intervals, digest time, cleanup schedule, rate limits, or
  delivery retry behavior.
- No replacement of GitHub Actions, Supabase, PTB, Telethon, or Anthropic.
- No speculative abstractions or broad dependency upgrades.

## Current problems

1. `src/userbot/monitor.py` is roughly 1,300 lines and combines subscriber
   selection, media handling, delivery state, digests, maintenance, polling, and
   runtime startup.
2. Its internal helpers are imported by tests and handlers, so a mechanical split
   without compatibility exports would cause unnecessary churn and regression risk.
3. `README.md` explains the product well but lacks a command reference, a complete
   environment-variable guide, a clear data flow, and an operational runbook.
4. `.env.example` still presents a Render webhook URL as the default even though
   production uses polling in GitHub Actions.
5. CI documentation and CI behavior differ: the documented lint command includes
   `scripts`, while `ci.yml` currently does not.
6. All workflows use mutable action tags and inherit repository-default token
   permissions.

## Chosen approach

Use a moderate structural refactor. Keep `src/userbot/monitor.py` as the stable
runtime facade and move coherent responsibilities into focused modules. Existing
imports from `monitor` remain valid through explicit re-exports. This gives most of
the readability benefit without rewriting the delivery engine.

The rejected alternatives are:

- cosmetic cleanup only: low risk, but leaves the main maintenance problem intact;
- full architectural rewrite: cleaner in theory, but too risky for a bot whose
  polling cursor, album buffering, media retries, and restart recovery are tightly
  coupled production invariants.

## Module boundaries

### `src/userbot/subscribers.py`

Owns subscription eligibility and soft channel-limit enforcement:

- batch calculation of allowed channel IDs;
- eligible subscriber lookup;
- digest-only flag calculation;
- language lookup used by pending delivery.

It depends on SQLAlchemy models and i18n, but not on Telethon or the Telegram bot.

### `src/userbot/media.py`

Owns media-specific policy and Bot API upload helpers:

- attachment classification based strictly on `message.media`;
- filename and size handling;
- caption budget checks;
- media download/upload;
- Bot API `file_id` cache and file-error classification.

The module keeps all existing timeout and caption-limit safeguards unchanged.

### `src/userbot/delivery.py`

Owns post persistence and per-user delivery state:

- album sibling absorption;
- message persistence and cursor advancement;
- AI-filter and auto-summary delivery paths;
- per-user/channel ordering;
- pending delivery persistence and startup replay;
- shutdown buffer flush and periodic buffer loop.

Its process-local buffers remain module-level state. They are not converted to new
classes in this pass because that would alter too many lifecycle assumptions at once.

### `src/userbot/digests.py`

Owns digest formatting, chunking, on-demand generation, daily generation, and the
digest scheduler loop. It preserves digest-only delivery and the current AI prompt.

### `src/userbot/maintenance.py`

Owns heartbeat, daily report claiming, subscription notices, paid-feature offers,
post/event cleanup, and their loops. Blocking DB operations remain behind
`asyncio.to_thread` where they currently run off the event loop.

### `src/userbot/monitor.py`

Becomes the polling/runtime facade:

- Telethon client startup and channel resolution;
- oldest-first catch-up polling and live-event registration;
- task startup and channel refresh;
- explicit compatibility exports for helpers still consumed by handlers/tests.

No wildcard re-export is allowed. The facade lists the compatibility surface so
future cleanup can remove it deliberately.

## Code hygiene

- Introduce narrow type aliases for repeated delivery tuple/dictionary shapes where
  they improve signatures without changing serialization.
- Normalize import grouping and multiline formatting, including handler exports.
- Remove only symbols proven unused by repository-wide search and test execution.
- Add missing generated/test artifacts to `.gitignore`.
- Keep broad exception handling only at deliberate resilience boundaries; document
  why those catches are fail-open or best-effort.
- Preserve the existing public and test-facing names during this refactor.

## GitHub Actions security hardening

Following the `cybersecurity-skills:securing-github-actions-workflows` checklist:

- pin `actions/checkout` and `actions/setup-python` to immutable commit SHAs, with
  human-readable version comments;
- add explicit least-privilege `permissions: contents: read` to every workflow;
- keep `pull_request` rather than `pull_request_target` for CI;
- keep secrets in step-level `env` and never interpolate event-controlled strings
  into shell commands;
- add Dependabot configuration for weekly GitHub Actions and pip updates;
- add `CODEOWNERS` coverage for workflow/config changes using `@0legRogovenko`;
- make CI run the same lint command documented for local development, including
  `scripts`.

The audit does not add deployment approvals because the Bot workflow must restart
automatically after a push. Repository branch-protection settings remain an operator
task because they cannot be enforced by source files alone.

## README and environment documentation

Rewrite `README.md` around two audiences:

1. users evaluating the bot;
2. maintainers deploying or operating it.

The updated README will contain:

- concise product description and actual Free/Basic/Pro matrix;
- command reference, including admin-only commands;
- delivery behavior for posts, albums, media, auto-summary, and digest-only mode;
- architecture and end-to-end data flow;
- local prerequisites and startup steps;
- required, optional, and watchdog-only variables in separate tables;
- Supabase/PostgreSQL connection guidance without real credentials;
- current GitHub Actions polling deployment and restart behavior;
- migrations, persistence, monitoring, healthcheck, and troubleshooting;
- security/privacy/legal limitations, license, and owner contacts.

Update `.env.example` so polling is the default and webhook mode is clearly optional.
The example must contain placeholders only.

## Compatibility and error handling

- External callers continue importing `start_userbot`, `refresh_channels`,
  `send_digest_now`, and `flush_buffer_on_shutdown` from `monitor`.
- Existing tests that intentionally exercise private monitor helpers may keep their
  imports during this pass; `monitor` re-exports those names explicitly.
- A Telegram send failure, media failure, AI failure, DB failure, or process restart
  must retain its current retry/fallback direction.
- No cleanup task may delete a post still referenced by pending delivery.
- No secret value may appear in README, examples, test output, or workflow logs.

## Verification

1. Establish the existing 185-test baseline before moving code.
2. Move one responsibility at a time and run its focused monitor/report tests.
3. Add import-contract tests for the public monitor facade and characterization tests
   only where an extracted boundary is not already covered.
4. Run the complete repository gates:

   ```bash
   venv/bin/python -m pytest tests/ -q -p no:cacheprovider
   venv/bin/python -m flake8 src tests main.py auth_userbot.py scripts
   git diff --check
   ```

5. Validate all workflow YAML and inspect the security checklist after edits.
6. Push `main`, require green CI, confirm the Bot job reaches
   `Start bot (polling mode)`, and require a successful external healthcheck with a
   fresh heartbeat.

## Rollback

The work is split into reviewable commits: characterization/compatibility tests,
module extraction, workflow hardening, and documentation. Since there is no schema
change, reverting those commits restores the previous code without data rollback.
