# TelegramWall Codebase Cleanup and README Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the oversized userbot runtime into focused modules, harden its GitHub Actions deployment, and replace stale setup documentation without changing bot behavior.

**Architecture:** `src/userbot/monitor.py` remains the stable runtime facade while subscriber selection, media, delivery, digests, and maintenance move into responsibility-focused modules. Compatibility imports protect handlers and existing tests; every extraction is mechanical and independently verified before the next one. Workflow hardening follows immutable-action and least-privilege guidance, while README and `.env.example` are rebuilt from the deployed configuration.

**Tech Stack:** Python 3.12, python-telegram-bot 21.9, Telethon 1.37, SQLAlchemy 2.0, PostgreSQL/Supabase, pytest, flake8, GitHub Actions.

---

## File map

### Create

- `src/userbot/subscribers.py` — subscriber eligibility and soft channel limits.
- `src/userbot/media.py` — media classification, limits, transfer, and file ID cache.
- `src/userbot/delivery.py` — post persistence, delivery, buffering, pending replay.
- `src/userbot/digests.py` — digest rendering, sending, and schedule.
- `src/userbot/maintenance.py` — heartbeat, reporting, notices, upsell, cleanup.
- `tests/test_userbot_facade.py` — compatibility contracts for extracted modules.
- `tests/test_workflow_security.py` — immutable actions and least-privilege checks.
- `tests/test_repository_docs.py` — README and environment-example accuracy checks.
- `.github/dependabot.yml` — weekly pip and GitHub Actions update checks.
- `.github/CODEOWNERS` — owner review coverage for workflows and dependency files.

### Modify

- `src/userbot/monitor.py` — polling/runtime facade and explicit re-exports.
- `tests/test_monitor.py` — patch extracted implementation modules where necessary.
- `tests/conftest.py` — route any new module-level DB session imports to the test DB.
- `src/bot/handlers/__init__.py` — readable, stable export formatting.
- `.github/workflows/bot.yml` — pinned actions and explicit permissions.
- `.github/workflows/ci.yml` — pinned actions, explicit permissions, matching lint scope.
- `.github/workflows/healthcheck.yml` — pinned actions and explicit permissions.
- `.gitignore` — generated test/coverage artifacts.
- `.env.example` — polling-first, complete, secret-free configuration.
- `README.md` — current product, setup, architecture, operations, and security guide.
- `CLAUDE.md` — module ownership and compatibility invariants.

## Task 1: Lock the runtime facade and establish the baseline

**Files:**
- Create: `tests/test_userbot_facade.py`

- [ ] **Step 1: Run the complete baseline**

Run:

```bash
venv/bin/python -m pytest tests/ -q -p no:cacheprovider
venv/bin/python -m flake8 src tests main.py auth_userbot.py scripts
git diff --check
```

Expected: `185 passed`; flake8 and diff check exit 0.

- [ ] **Step 2: Add the facade characterization test**

Create `tests/test_userbot_facade.py`:

```python
"""Stable imports exposed by the userbot runtime facade."""
from src.userbot import monitor


PUBLIC_RUNTIME_API = (
    "start_userbot",
    "refresh_channels",
    "send_digest_now",
    "flush_buffer_on_shutdown",
)

COMPATIBILITY_HELPERS = (
    "MAX_MESSAGE_CHARS",
    "_split_message",
    "_get_eligible_subscribers",
    "_get_eligible_subscriber_details",
    "_get_media_type",
    "_media_filename",
    "_media_size_ok",
    "_cache_file_id",
    "_cleanup_old_posts",
)


def test_monitor_exposes_runtime_api():
    for name in PUBLIC_RUNTIME_API:
        assert callable(getattr(monitor, name))


def test_monitor_keeps_tested_compatibility_helpers():
    for name in COMPATIBILITY_HELPERS:
        assert hasattr(monitor, name)
```

- [ ] **Step 3: Run the characterization test**

Run:

```bash
venv/bin/python -m pytest tests/test_userbot_facade.py -q -p no:cacheprovider
```

Expected: `2 passed`. This test starts green because it records the interface that
must survive the refactor.

- [ ] **Step 4: Commit the contract**

```bash
git add tests/test_userbot_facade.py
git commit -m "Test userbot runtime facade"
```

## Task 2: Extract subscriber eligibility

**Files:**
- Create: `src/userbot/subscribers.py`
- Modify: `src/userbot/monitor.py:126-215`
- Modify: `tests/test_userbot_facade.py`

- [ ] **Step 1: Write the failing module-boundary test**

Append:

```python
def test_subscriber_helpers_come_from_focused_module():
    from src.userbot import subscribers

    assert monitor._get_eligible_subscribers is subscribers.get_eligible_subscribers
    assert (
        monitor._get_eligible_subscriber_details
        is subscribers.get_eligible_subscriber_details
    )
```

- [ ] **Step 2: Verify RED**

Run:

```bash
venv/bin/python -m pytest tests/test_userbot_facade.py::test_subscriber_helpers_come_from_focused_module -q -p no:cacheprovider
```

Expected: FAIL with `ImportError` because `src.userbot.subscribers` does not exist.

- [ ] **Step 3: Create the subscriber module**

Create `src/userbot/subscribers.py` with this header and public surface:

```python
"""Subscriber eligibility and soft channel-limit enforcement."""
import logging

from sqlalchemy.orm import joinedload

from src.bot.i18n import lang_of
from src.models import User, UserChannel

logger = logging.getLogger(__name__)

__all__ = [
    "get_eligible_subscriber_details",
    "get_eligible_subscribers",
    "user_lang",
]
```

Move the existing bodies of these functions without changing queries, ordering,
return shapes, logging, or comments:

```text
_batch_allowed_channel_ids  -> _batch_allowed_channel_ids
_get_eligible_subscribers   -> get_eligible_subscribers
_get_eligible_subscriber_details -> get_eligible_subscriber_details
_user_lang                  -> user_lang
```

Update calls inside the moved functions to their new non-underscored public names.

- [ ] **Step 4: Re-export compatibility names from monitor**

Replace the removed definitions with:

```python
from src.userbot.subscribers import (
    get_eligible_subscriber_details as _get_eligible_subscriber_details,
    get_eligible_subscribers as _get_eligible_subscribers,
    user_lang as _user_lang,
)
```

- [ ] **Step 5: Run focused and monitor tests**

```bash
venv/bin/python -m pytest tests/test_userbot_facade.py tests/test_monitor.py -q -p no:cacheprovider
venv/bin/python -m flake8 src/userbot/subscribers.py src/userbot/monitor.py tests/test_userbot_facade.py
```

Expected: all selected tests pass; flake8 exits 0.

- [ ] **Step 6: Commit**

```bash
git add src/userbot/subscribers.py src/userbot/monitor.py tests/test_userbot_facade.py
git commit -m "Extract subscriber eligibility service"
```

## Task 3: Extract media handling

**Files:**
- Create: `src/userbot/media.py`
- Modify: `src/userbot/monitor.py:70-125,215-299`
- Modify: `tests/test_userbot_facade.py`

- [ ] **Step 1: Write the failing media-boundary test**

Append:

```python
def test_media_helpers_come_from_focused_module():
    from src.userbot import media

    assert monitor._get_media_type is media.get_media_type
    assert monitor._media_filename is media.media_filename
    assert monitor._media_size_ok is media.media_size_ok
    assert monitor._cache_file_id is media.cache_file_id
    assert monitor._media_file_ids is media.MEDIA_FILE_IDS
```

- [ ] **Step 2: Verify RED**

```bash
venv/bin/python -m pytest tests/test_userbot_facade.py::test_media_helpers_come_from_focused_module -q -p no:cacheprovider
```

Expected: FAIL because `src.userbot.media` is absent.

- [ ] **Step 3: Create the media module and move exact behavior**

Create the module with:

```python
"""Telegram media classification, limits, transfer, and file ID reuse."""
import html
import io
import logging
import re

from telethon import TelegramClient
from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    DocumentAttributeVideo,
    MessageMediaDocument,
    MessageMediaPhoto,
)

from src.config import config

logger = logging.getLogger(__name__)

MEDIA_CACHE_MAX = 500
MEDIA_FILE_IDS: dict[int, str] = {}
CAPTION_TEXT_BUDGET = 900

__all__ = [
    "MEDIA_CACHE_MAX",
    "MEDIA_FILE_IDS",
    "cache_file_id",
    "caption_fits",
    "caption_text_budget",
    "download_media_bytes",
    "get_media_type",
    "is_file_error",
    "media_filename",
    "media_size_ok",
    "send_media",
]
```

Move the existing function bodies unchanged and rename only their definitions:

```text
_caption_text_budget   -> caption_text_budget
_caption_fits          -> caption_fits
_get_media_type        -> get_media_type
_media_filename        -> media_filename
_media_size_ok         -> media_size_ok
_download_media_bytes  -> download_media_bytes
_send_media            -> send_media
_is_file_error         -> is_file_error
_cache_file_id         -> cache_file_id
```

Within `cache_file_id`, replace `_media_file_ids`/`_MEDIA_CACHE_MAX` with
`MEDIA_FILE_IDS`/`MEDIA_CACHE_MAX`. Preserve the 120-second media timeouts and the
`message.media`-only classifier.

- [ ] **Step 4: Re-export old names from monitor**

```python
from src.userbot.media import (
    MEDIA_CACHE_MAX as _MEDIA_CACHE_MAX,
    MEDIA_FILE_IDS as _media_file_ids,
    cache_file_id as _cache_file_id,
    caption_fits as _caption_fits,
    caption_text_budget as _caption_text_budget,
    download_media_bytes as _download_media_bytes,
    get_media_type as _get_media_type,
    is_file_error as _is_file_error,
    media_filename as _media_filename,
    media_size_ok as _media_size_ok,
    send_media as _send_media,
)
```

- [ ] **Step 5: Verify media behavior**

```bash
venv/bin/python -m pytest tests/test_userbot_facade.py tests/test_monitor.py -q -p no:cacheprovider
venv/bin/python -m flake8 src/userbot/media.py src/userbot/monitor.py
```

Expected: media classification, caption, cache, and size tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/userbot/media.py src/userbot/monitor.py tests/test_userbot_facade.py
git commit -m "Extract userbot media handling"
```

## Task 4: Extract delivery and restart recovery

**Files:**
- Create: `src/userbot/delivery.py`
- Modify: `src/userbot/monitor.py:300-812`
- Modify: `tests/test_userbot_facade.py`
- Modify: `tests/test_monitor.py:330-420`
- Modify: `tests/conftest.py:45-65`

- [ ] **Step 1: Write the failing delivery-boundary test**

Append:

```python
def test_delivery_runtime_comes_from_focused_module():
    from src.userbot import delivery

    assert monitor._process_message is delivery.process_message
    assert monitor.flush_buffer_on_shutdown is delivery.flush_buffer_on_shutdown
    assert monitor._flush_pending is delivery.flush_pending
```

- [ ] **Step 2: Verify RED**

```bash
venv/bin/python -m pytest tests/test_userbot_facade.py::test_delivery_runtime_comes_from_focused_module -q -p no:cacheprovider
```

Expected: FAIL because `src.userbot.delivery` is absent.

- [ ] **Step 3: Create delivery.py**

Use this dependency header:

```python
"""Post persistence, ordered delivery, buffering, and restart recovery."""
import asyncio
import html
import logging
import time

from telethon import TelegramClient
from telethon.tl.types import Message

from src.bot.i18n import lang_of, t
from src.bot.keyboards import summary_button
from src.database import db_session, get_session
from src.models import Channel, PendingPost, Post, User
from src.services import metrics
from src.services.summarizer import summarize
from src.userbot.media import (
    MEDIA_FILE_IDS,
    cache_file_id,
    caption_fits,
    caption_text_budget,
    download_media_bytes,
    get_media_type,
    is_file_error,
    media_filename,
    media_size_ok,
    send_media,
)
from src.userbot.subscribers import get_eligible_subscriber_details, user_lang

logger = logging.getLogger(__name__)

DELIVERY_GRACE_SECS = 20
FLUSH_TICK_SECS = 10
DELIVERY_CONCURRENCY = 3
DELIVERY_SEMAPHORE = asyncio.Semaphore(DELIVERY_CONCURRENCY)
IN_FLIGHT: dict[tuple[int, int], dict] = {}
BATCH_BUFFER: dict[tuple[int, int], dict] = {}
```

Move these complete bodies from `monitor.py` and rename only the definitions:

```text
_absorb_album_sibling    -> absorb_album_sibling
_process_message         -> process_message
_deliver_to_user         -> deliver_to_user
_send_summary            -> send_summary
_queue_pending           -> queue_pending
flush_buffer_on_shutdown -> flush_buffer_on_shutdown
_entry_due               -> entry_due
_deliver_entry           -> deliver_entry
_batch_flush_loop        -> batch_flush_loop
_flush_pending           -> flush_pending
```

Apply these mechanical global-name replacements throughout the moved bodies:

```text
_batch_buffer -> BATCH_BUFFER
_in_flight -> IN_FLIGHT
_delivery_sem -> DELIVERY_SEMAPHORE
_media_file_ids -> MEDIA_FILE_IDS
_get_media_type -> get_media_type
_media_filename -> media_filename
_media_size_ok -> media_size_ok
_download_media_bytes -> download_media_bytes
_send_media -> send_media
_is_file_error -> is_file_error
_cache_file_id -> cache_file_id
_caption_text_budget -> caption_text_budget
_caption_fits -> caption_fits
_get_eligible_subscriber_details -> get_eligible_subscriber_details
_user_lang -> user_lang
_absorb_album_sibling -> absorb_album_sibling
_deliver_to_user -> deliver_to_user
_send_summary -> send_summary
_queue_pending -> queue_pending
_entry_due -> entry_due
_deliver_entry -> deliver_entry
```

Do not change exception boundaries, pending-row deletion timing, album grace, message
ordering, fallback direction, or metrics calls.

- [ ] **Step 4: Re-export delivery names from monitor**

```python
from src.userbot.delivery import (
    BATCH_BUFFER as _batch_buffer,
    DELIVERY_GRACE_SECS,
    IN_FLIGHT as _in_flight,
    batch_flush_loop as _batch_flush_loop,
    flush_buffer_on_shutdown,
    flush_pending as _flush_pending,
    process_message as _process_message,
)
```

- [ ] **Step 5: Point tests at implementation-owned monkeypatches**

In album tests, import `delivery` and patch these exact targets:

```python
monkeypatch.setattr(delivery, "get_session", lambda: db)
monkeypatch.setattr(delivery, "get_eligible_subscriber_details", lambda *a: [])
```

Add `"src.userbot.delivery.db_session"` to `patch_db_session.targets` in
`tests/conftest.py`. Keep polling tests patching `monitor._process_message`, because
the polling facade consumes that compatibility alias.

- [ ] **Step 6: Verify delivery invariants**

```bash
venv/bin/python -m pytest tests/test_userbot_facade.py tests/test_monitor.py -q -p no:cacheprovider
venv/bin/python -m flake8 src/userbot/delivery.py src/userbot/monitor.py tests/test_monitor.py tests/conftest.py
```

Expected: album, media, pending delivery, ordering, and polling catch-up tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/userbot/delivery.py src/userbot/monitor.py tests/test_userbot_facade.py tests/test_monitor.py tests/conftest.py
git commit -m "Extract ordered delivery runtime"
```

## Task 5: Extract digests

**Files:**
- Create: `src/userbot/digests.py`
- Modify: `src/userbot/monitor.py:996-1135`
- Modify: `tests/test_userbot_facade.py`

- [ ] **Step 1: Write the failing digest-boundary test**

Append:

```python
def test_digest_runtime_comes_from_focused_module():
    from src.userbot import digests

    assert monitor._split_message is digests.split_message
    assert monitor.send_digest_now is digests.send_digest_now
    assert monitor._digest_loop is digests.digest_loop
```

- [ ] **Step 2: Verify RED**

```bash
venv/bin/python -m pytest tests/test_userbot_facade.py::test_digest_runtime_comes_from_focused_module -q -p no:cacheprovider
```

Expected: FAIL because `src.userbot.digests` is absent.

- [ ] **Step 3: Create digests.py**

Use:

```python
"""AI digest formatting, generation, delivery, and scheduling."""
import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone

from src.bot.i18n import lang_of, t
from src.config import config
from src.database import get_session
from src.models import Post, User, UserChannel
from src.services.summarizer import build_digest

logger = logging.getLogger(__name__)
MAX_MESSAGE_CHARS = 3900
```

Move and mechanically rename:

```text
_split_message          -> split_message
_digest_html            -> digest_html
_build_and_send_digest  -> build_and_send_digest
send_digest_now         -> send_digest_now
_send_daily_digest      -> send_daily_digest
_digest_loop            -> digest_loop
```

Update only internal references to the renamed functions. Preserve 24-hour selection,
eight-post source cap, source grouping, message splitting, and configured UTC hour.

- [ ] **Step 4: Re-export digest names from monitor**

```python
from src.userbot.digests import (
    MAX_MESSAGE_CHARS,
    digest_loop as _digest_loop,
    send_digest_now,
    split_message as _split_message,
)
```

- [ ] **Step 5: Verify**

```bash
venv/bin/python -m pytest tests/test_userbot_facade.py tests/test_monitor.py tests/test_callbacks.py -q -p no:cacheprovider
venv/bin/python -m flake8 src/userbot/digests.py src/userbot/monitor.py
```

- [ ] **Step 6: Commit**

```bash
git add src/userbot/digests.py src/userbot/monitor.py tests/test_userbot_facade.py
git commit -m "Extract AI digest runtime"
```

## Task 6: Extract maintenance loops

**Files:**
- Create: `src/userbot/maintenance.py`
- Modify: `src/userbot/monitor.py:813-995`
- Modify: `tests/test_userbot_facade.py`

- [ ] **Step 1: Write the failing maintenance-boundary test**

Append:

```python
def test_cleanup_comes_from_maintenance_module():
    from src.userbot import maintenance

    assert monitor._cleanup_old_posts is maintenance.cleanup_old_posts
    assert monitor._heartbeat_loop is maintenance.heartbeat_loop
    assert monitor._upsell_loop is maintenance.upsell_loop
```

- [ ] **Step 2: Verify RED**

```bash
venv/bin/python -m pytest tests/test_userbot_facade.py::test_cleanup_comes_from_maintenance_module -q -p no:cacheprovider
```

Expected: FAIL because `src.userbot.maintenance` is absent.

- [ ] **Step 3: Create maintenance.py**

Use:

```python
"""Operational heartbeat, reports, notices, offers, and data cleanup."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from src.config import config
from src.database import get_session
from src.models import BotEvent, BotHealth, PendingPost, Post
from src.services import metrics

logger = logging.getLogger(__name__)
```

Move and mechanically rename:

```text
_heartbeat_tick            -> heartbeat_tick
_heartbeat_loop            -> heartbeat_loop
_report_due                -> report_due
_mark_report_sent          -> mark_report_sent
_claim_report              -> claim_report
_report_loop               -> report_loop
_subscription_notice_loop  -> subscription_notice_loop
_upsell_loop               -> upsell_loop
_cleanup_old_posts         -> cleanup_old_posts
_run_cleanup_once          -> run_cleanup_once
_cleanup_loop              -> cleanup_loop
```

Update internal references only. Keep report claim-before-send, pending-post exclusion,
500-row deletion chunks, metrics flushing, loop delays, and `asyncio.to_thread` calls.

- [ ] **Step 4: Re-export maintenance names from monitor**

```python
from src.userbot.maintenance import (
    cleanup_loop as _cleanup_loop,
    cleanup_old_posts as _cleanup_old_posts,
    heartbeat_loop as _heartbeat_loop,
    report_loop as _report_loop,
    subscription_notice_loop as _subscription_notice_loop,
    upsell_loop as _upsell_loop,
)
```

- [ ] **Step 5: Verify maintenance behavior**

```bash
venv/bin/python -m pytest tests/test_userbot_facade.py tests/test_monitor.py tests/test_report.py tests/test_subscription_notifier.py tests/test_upsell.py -q -p no:cacheprovider
venv/bin/python -m flake8 src/userbot/maintenance.py src/userbot/monitor.py
```

- [ ] **Step 6: Commit**

```bash
git add src/userbot/maintenance.py src/userbot/monitor.py tests/test_userbot_facade.py
git commit -m "Extract userbot maintenance loops"
```

## Task 7: Finish the runtime facade and repository hygiene

**Files:**
- Modify: `src/userbot/monitor.py`
- Modify: `src/bot/handlers/__init__.py`
- Modify: `.gitignore`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Reduce monitor to runtime responsibilities**

Keep only Telethon startup, `_poll_one_channel`, `_poll_channels`,
`_register_live_handler`, `_resolve_channels`, `start_userbot`, and
`refresh_channels`, plus explicit imports/re-exports from Tasks 2–6.

Add this public declaration:

```python
__all__ = [
    "flush_buffer_on_shutdown",
    "refresh_channels",
    "send_digest_now",
    "start_userbot",
]
```

Remove imports no longer used by the facade. Keep polling constants and `_client` in
`monitor.py`.

- [ ] **Step 2: Format handler package exports**

Rewrite `src/bot/handlers/__init__.py::__all__` as one quoted symbol per line, sorted
within the public handler group and the compatibility helper group. Do not add or
remove exported names.

- [ ] **Step 3: Ignore generated artifacts**

Append to `.gitignore`:

```gitignore
.pytest_cache/
.coverage
htmlcov/
coverage.xml
```

- [ ] **Step 4: Document module ownership**

Replace the single-monitor description in `CLAUDE.md` with:

```markdown
- `src/userbot/monitor.py` — Telethon polling and runtime facade.
- `src/userbot/subscribers.py` — eligibility and channel limits.
- `src/userbot/media.py` — attachment policy and Bot API media reuse.
- `src/userbot/delivery.py` — persistence, ordering, retries, pending replay.
- `src/userbot/digests.py` — AI digest generation and schedule.
- `src/userbot/maintenance.py` — heartbeat, reports, notices, cleanup.
```

- [ ] **Step 5: Run complete Python verification**

```bash
venv/bin/python -m pytest tests/ -q -p no:cacheprovider
venv/bin/python -m flake8 src tests main.py auth_userbot.py scripts
git diff --check
```

Expected: all tests pass and `src/userbot/monitor.py` is substantially smaller than
its original 1,309 lines.

- [ ] **Step 6: Commit**

```bash
git add src/userbot/monitor.py src/bot/handlers/__init__.py .gitignore CLAUDE.md
git commit -m "Polish userbot runtime facade"
```

## Task 8: Harden GitHub Actions

**Files:**
- Create: `tests/test_workflow_security.py`
- Create: `.github/dependabot.yml`
- Create: `.github/CODEOWNERS`
- Modify: `.github/workflows/bot.yml`
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/healthcheck.yml`

- [ ] **Step 1: Write failing workflow-security tests**

Create:

```python
"""Static security contracts for GitHub Actions workflows."""
import re
from pathlib import Path


WORKFLOW_DIR = Path(__file__).parents[1] / ".github" / "workflows"
WORKFLOWS = tuple(sorted(WORKFLOW_DIR.glob("*.yml")))
MUTABLE_ACTION = re.compile(r"uses:\s+[^\s]+@v\d+(?:\s|$)")


def test_workflows_pin_actions_to_commit_sha():
    violations = []
    for path in WORKFLOWS:
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if MUTABLE_ACTION.search(line):
                violations.append(f"{path.name}:{number}: {line.strip()}")
    assert violations == []


def test_workflows_declare_read_only_contents_permission():
    for path in WORKFLOWS:
        text = path.read_text()
        assert re.search(r"^permissions:\n\s+contents:\s+read$", text, re.MULTILINE), path


def test_ci_does_not_use_pull_request_target():
    text = (WORKFLOW_DIR / "ci.yml").read_text()
    assert "pull_request_target" not in text


def test_ci_lints_scripts_directory():
    text = (WORKFLOW_DIR / "ci.yml").read_text()
    assert "flake8 src tests main.py auth_userbot.py scripts" in text
```

- [ ] **Step 2: Verify RED**

```bash
venv/bin/python -m pytest tests/test_workflow_security.py -q -p no:cacheprovider
```

Expected: failures for mutable action tags, missing permissions, and missing `scripts`
in the CI lint command.

- [ ] **Step 3: Pin official actions and minimize permissions**

In all three workflows add directly after `name`:

```yaml
permissions:
  contents: read
```

Replace every checkout/setup reference with the immutable release commits verified
from the official repositories on 2026-08-29:

```yaml
- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
- uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
```

Change the CI lint step to:

```yaml
- name: Lint
  run: flake8 src tests main.py auth_userbot.py scripts
```

- [ ] **Step 4: Add automated dependency maintenance**

Create `.github/dependabot.yml`:

```yaml
version: 2
updates:
  - package-ecosystem: github-actions
    directory: "/"
    schedule:
      interval: weekly
    open-pull-requests-limit: 5
  - package-ecosystem: pip
    directory: "/"
    schedule:
      interval: weekly
    open-pull-requests-limit: 5
```

Create `.github/CODEOWNERS`:

```text
.github/workflows/ @0legRogovenko
.github/dependabot.yml @0legRogovenko
requirements*.txt @0legRogovenko
```

- [ ] **Step 5: Verify workflows**

```bash
venv/bin/python -m pytest tests/test_workflow_security.py -q -p no:cacheprovider
venv/bin/python -m flake8 tests/test_workflow_security.py
git diff --check
```

Expected: four tests pass; no mutable `@vN` action references remain.

- [ ] **Step 6: Commit**

```bash
git add .github tests/test_workflow_security.py
git commit -m "Harden GitHub Actions workflows"
```

## Task 9: Rebuild README and environment guide from current behavior

**Files:**
- Create: `tests/test_repository_docs.py`
- Modify: `README.md`
- Modify: `.env.example`

- [ ] **Step 1: Write failing documentation contracts**

Create:

```python
"""Repository documentation must match the deployed runtime."""
from pathlib import Path


ROOT = Path(__file__).parents[1]
README = (ROOT / "README.md").read_text()
ENV_EXAMPLE = (ROOT / ".env.example").read_text()


def test_readme_has_operator_sections():
    required = (
        "## Команды",
        "## Как работает доставка",
        "## Переменные окружения",
        "## Локальный запуск",
        "## Деплой через GitHub Actions",
        "## Мониторинг и устранение неполадок",
    )
    for heading in required:
        assert heading in README


def test_environment_example_defaults_to_polling():
    assert "TELEGRAM_WEBHOOK_URL=\n" in ENV_EXAMPLE
    assert "your-app.onrender.com" not in ENV_EXAMPLE


def test_environment_example_documents_runtime_controls():
    required = (
        "TELEGRAM_WEBHOOK_SECRET",
        "CLAUDE_MODEL",
        "CLAUDE_FILTER_MODEL",
        "AI_DAILY_SUMMARY_LIMIT",
        "UPSELL_INTERVAL_DAYS",
        "UPSELL_BATCH_SIZE",
        "ADMIN_REPORT_HOUR_UTC",
    )
    for key in required:
        assert key in ENV_EXAMPLE
```

- [ ] **Step 2: Verify RED**

```bash
venv/bin/python -m pytest tests/test_repository_docs.py -q -p no:cacheprovider
```

Expected: failures for missing operator headings and the stale Render webhook example.

- [ ] **Step 3: Replace `.env.example` with polling-first configuration**

Use empty or unmistakably synthetic values only. The opening block must be:

```dotenv
# Required: Telegram Bot API
TELEGRAM_BOT_TOKEN=1234567890:replace-with-bot-token
TELEGRAM_WEBHOOK_URL=
# TELEGRAM_WEBHOOK_SECRET=replace-only-for-webhook-mode
TELEGRAM_ADMIN_IDS=123456789

# Required: Telethon reader account
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=replace-with-api-hash
TELEGRAM_PHONE=+79000000000
TELEGRAM_SESSION_STRING=replace-with-string-session

# Required: persistent PostgreSQL (Supabase Session Pooler example)
DATABASE_URL=postgresql://postgres.PROJECT_REF:replace-with-password@aws-0-REGION.pooler.supabase.com:5432/postgres?sslmode=require
```

Keep Anthropic and YooKassa clearly optional. List every existing configurable limit,
price, schedule, media, blocklist, report, Flask, and model variable exactly once.
Explain that watchdog thresholds are GitHub repository Variables, not bot environment
variables.

- [ ] **Step 4: Rewrite README around the actual system**

Keep badges, contact details, AGPL terms, and legal cautions. Use this exact section
order:

```markdown
# TelegramWall
## Возможности и тарифы
## Команды
## Как работает доставка
## Архитектура
## AI и обработка данных
## Переменные окружения
## Локальный запуск
## Проверка изменений
## Деплой через GitHub Actions
## Мониторинг и устранение неполадок
## Безопасность и правовые ограничения
## Лицензия
## Автор и контакты
```

The command table must distinguish common, paid, and admin-only commands. The data
flow must state that Telethon reads public channels without joining, PostgreSQL keeps
cursors/settings/pending work, and PTB sends content to users. The deployment section
must state that production omits `TELEGRAM_WEBHOOK_URL`, restarts every two hours, and
uses a 350-minute job timeout. The troubleshooting section must include duplicate
polling, stale heartbeat, missing posts, failed AI, and media-size fallback.

- [ ] **Step 5: Verify documentation**

```bash
venv/bin/python -m pytest tests/test_repository_docs.py -q -p no:cacheprovider
venv/bin/python -m flake8 tests/test_repository_docs.py
git diff --check
```

Expected: three documentation tests pass.

- [ ] **Step 6: Commit**

```bash
git add README.md .env.example tests/test_repository_docs.py
git commit -m "Refresh TelegramWall operator documentation"
```

## Task 10: Full verification, security audit, and deployment

**Files:** all modified files from Tasks 1–9.

- [ ] **Step 1: Run the full local gate**

```bash
venv/bin/python -m pytest tests/ -q -p no:cacheprovider
venv/bin/python -m flake8 src tests main.py auth_userbot.py scripts
git diff --check
```

Expected: at least 199 tests pass; lint and diff checks exit 0.

- [ ] **Step 2: Confirm the refactor and security invariants**

```bash
wc -l src/userbot/monitor.py src/userbot/*.py
rg -n "uses: .*@v[0-9]" .github/workflows
git grep -nE "(sk-ant-api|postgres(ql)?://[^[:space:]]+:[^@[:space:]]+@)" -- ':!README.md' ':!.env.example'
git status --short
```

Expected:

- `monitor.py` is below 400 lines;
- the mutable-action search returns no matches;
- the credential-pattern search returns no real credential;
- only intended files are staged/tracked, and the user's untracked `AGENTS.md` remains untouched.

- [ ] **Step 3: Commit any final formatting-only correction**

Only if Step 1 or Step 2 required a correction: stage each corrected file by its
literal path as shown by `git status --short` (do not use `git add -A`), then run:

```bash
git commit -m "Polish refactor verification fixes"
```

If no correction was needed, do not create an empty commit.

- [ ] **Step 4: Push main**

```bash
git push origin main
```

- [ ] **Step 5: Verify GitHub Actions**

```bash
gh run list --repo 0legRogovenko/TelegramWall --branch main --limit 10
```

Require the new CI run to complete successfully. Require the new Bot run to reach an
in-progress `Start bot (polling mode)` step.

- [ ] **Step 6: Verify the external heartbeat**

```bash
gh workflow run healthcheck.yml --repo 0legRogovenko/TelegramWall
gh run list --repo 0legRogovenko/TelegramWall --workflow Healthcheck --limit 1
```

Wait for completion and inspect its log. Require either `OK — last seen N min ago` or,
on the first post-outage run, `Bot recovered — notice sent`; after recovery, run it a
second time and require the normal `OK` line.
