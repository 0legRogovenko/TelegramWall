# Paid Feature Upsell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send useful paid-feature offers to active Free users at most once every three days, while keeping contextual upgrade prompts immediate and preventing promotion spam.

**Architecture:** Persist one cooldown timestamp on `User`, select a bounded batch of eligible Free users in a focused upsell service, and send localized rotating campaigns from an hourly background loop. Reuse existing payment callbacks and prices, update the cooldown only after Telegram accepts a scheduled or contextual offer, and count successful offers in the daily admin report.

**Tech Stack:** Python 3.12, python-telegram-bot 21.9, SQLAlchemy 2.0, PostgreSQL/SQLite, pytest-asyncio, GitHub Actions.

---

## File map

- Create `src/services/upsell.py`: eligibility, campaign rendering, sending, cooldown updates.
- Create `tests/test_upsell.py`: service, localization, keyboard, retries, and batch tests.
- Modify `src/models.py`: persisted cooldown timestamp.
- Modify `src/database.py`: additive migration for an existing database.
- Modify `src/config.py` and `.env.example`: interval and batch configuration.
- Modify `src/bot/i18n.py`: three campaign variants in Russian, English, and Spanish.
- Modify `src/bot/keyboards.py`: trial-aware monthly-plan keyboard.
- Modify `src/userbot/monitor.py`: hourly scheduler loop.
- Modify `src/bot/handlers/channels.py`, `src/bot/handlers/ai.py`, `src/bot/handlers/buttons.py`, and `src/bot/handlers/callbacks.py`: advance cooldown after contextual offers.
- Modify `src/services/subscription_notifier.py`: count a successful expiration notice as an offer.
- Modify `src/services/metrics.py` and `src/services/report.py`: track and report successful offers.
- Modify `tests/conftest.py`, `tests/test_handlers.py`, `tests/test_callbacks.py`, `tests/test_subscription_notifier.py`, and `tests/test_report.py`: integration coverage and shared test DB routing.
- Modify `README.md` and `CLAUDE.md`: document product behavior and invariants.

### Task 1: Persist the upsell cooldown and configuration

**Files:**
- Modify: `tests/test_models.py`
- Modify: `src/models.py:18-45`
- Modify: `src/database.py:40-75`
- Modify: `src/config.py:60-85`
- Modify: `.env.example:24-40`

- [ ] **Step 1: Write the failing model-mapping test**

Add to `tests/test_models.py`:

```python
from sqlalchemy import inspect as sa_inspect


def test_user_maps_upsell_cooldown_column():
    assert "upsell_last_sent_at" in sa_inspect(User).columns.keys()
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
venv/bin/python -m pytest tests/test_models.py::test_user_maps_upsell_cooldown_column -q
```

Expected: FAIL because `upsell_last_sent_at` is absent from the mapped columns.

- [ ] **Step 3: Add the minimal persisted state and defaults**

Add to `User` in `src/models.py`:

```python
upsell_last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

Add to `init_db()` in `src/database.py`:

```python
_run_migration(
    conn, "ALTER TABLE users ADD COLUMN upsell_last_sent_at TIMESTAMPTZ"
)
```

Add to `Config` in `src/config.py`:

```python
UPSELL_INTERVAL_DAYS: int = max(1, int(os.getenv("UPSELL_INTERVAL_DAYS", "3")))
UPSELL_BATCH_SIZE: int = max(1, int(os.getenv("UPSELL_BATCH_SIZE", "50")))
```

Document both optional variables in `.env.example`:

```dotenv
# UPSELL_INTERVAL_DAYS=3      # реклама платных функций для Free
# UPSELL_BATCH_SIZE=50        # максимум сообщений за один часовой проход
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```bash
venv/bin/python -m pytest tests/test_models.py::test_user_maps_upsell_cooldown_column -q
```

Expected: `1 passed`.

- [ ] **Step 5: Commit the persistence slice**

```bash
git add tests/test_models.py src/models.py src/database.py src/config.py .env.example
git commit -m "Add persisted upsell cooldown"
```

### Task 2: Select eligible users and render localized campaigns

**Files:**
- Create: `tests/test_upsell.py`
- Create: `src/services/upsell.py`
- Modify: `src/bot/i18n.py:190-230`
- Modify: `src/bot/keyboards.py:40-75`
- Modify: `tests/conftest.py:35-75`

- [ ] **Step 1: Write failing eligibility and batch tests**

Create `tests/test_upsell.py` with these initial tests:

```python
from datetime import datetime, timedelta, timezone

import pytest

from src.config import config
from src.models import Subscription, User, UserChannel
from tests.conftest import create_channel, create_subscription, create_user


def _due_upsells():
    try:
        from src.services.upsell import due_upsells
    except ImportError:
        pytest.fail("upsell service is not implemented")
    return due_upsells


@pytest.fixture(autouse=True)
def _clean_upsell_users(db):
    user_ids = [
        row[0] for row in db.query(User.id).filter(
            User.telegram_id.between(9600, 9699)
        ).all()
    ]
    if user_ids:
        db.query(UserChannel).filter(UserChannel.user_id.in_(user_ids)).delete(
            synchronize_session=False
        )
        db.query(Subscription).filter(Subscription.user_id.in_(user_ids)).delete(
            synchronize_session=False
        )
        db.query(User).filter(User.id.in_(user_ids)).delete(synchronize_session=False)
        db.commit()


def _active_channel(db, user, suffix: str):
    channel = create_channel(db, f"upsell_{suffix}")
    db.add(UserChannel(user_id=user.id, channel_id=channel.id, is_active=True))
    db.commit()


def test_selects_old_active_free_user(db):
    now = datetime.now(timezone.utc)
    user = create_user(
        db,
        telegram_id=9601,
        language="ru",
        created_at=now - timedelta(days=config.UPSELL_INTERVAL_DAYS + 1),
    )
    _active_channel(db, user, "eligible")

    result = _due_upsells()(db, now=now)

    assert [item.telegram_id for item in result if item.telegram_id == 9601] == [9601]


def test_excludes_new_inactive_paid_and_admin_users(db, monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(config, "ADMIN_IDS", [9605])
    new_user = create_user(db, telegram_id=9602, language="ru", created_at=now)
    inactive = create_user(
        db, telegram_id=9603, language="ru",
        created_at=now - timedelta(days=10),
    )
    paid = create_user(
        db, telegram_id=9604, language="ru",
        created_at=now - timedelta(days=10),
    )
    admin = create_user(
        db, telegram_id=9605, language="ru",
        created_at=now - timedelta(days=10),
    )
    for user, suffix in ((new_user, "new"), (paid, "paid"), (admin, "admin")):
        _active_channel(db, user, suffix)
    create_subscription(db, paid, tier="basic")

    ids = {item.telegram_id for item in _due_upsells()(db, now=now)}

    assert new_user.telegram_id not in ids
    assert inactive.telegram_id not in ids
    assert paid.telegram_id not in ids
    assert admin.telegram_id not in ids


def test_respects_cooldown_and_batch_limit(db, monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(config, "UPSELL_BATCH_SIZE", 2)
    for index in range(3):
        user = create_user(
            db,
            telegram_id=9610 + index,
            language="en",
            created_at=now - timedelta(days=10),
        )
        _active_channel(db, user, f"batch_{index}")
    cooling = create_user(
        db,
        telegram_id=9613,
        language="en",
        created_at=now - timedelta(days=10),
        upsell_last_sent_at=now - timedelta(days=1),
    )
    _active_channel(db, cooling, "cooling")

    result = _due_upsells()(db, now=now)

    assert len(result) == 2
    assert cooling.telegram_id not in {item.telegram_id for item in result}
```

Add `"src.services.upsell.db_session"` to the patched targets in
`tests/conftest.py` after the service exists.

- [ ] **Step 2: Run eligibility tests and verify RED**

Run:

```bash
venv/bin/python -m pytest tests/test_upsell.py -q
```

Expected: FAIL with `upsell service is not implemented`.

- [ ] **Step 3: Implement the candidate query and deterministic variant**

Create `src/services/upsell.py` with this public core:

```python
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import exists

from src.bot.i18n import lang_of, t
from src.bot.payments import price_label
from src.config import config
from src.database import db_session
from src.models import Subscription, User, UserChannel
CAMPAIGN_KEYS = ("upsell_summary", "upsell_digest", "upsell_capacity")


@dataclass(frozen=True)
class UpsellCandidate:
    user_id: int
    telegram_id: int
    lang: str
    trial_used: bool
    variant: int


def _variant(user_id: int, now: datetime) -> int:
    period = now.date().toordinal() // config.UPSELL_INTERVAL_DAYS
    return (user_id + period) % len(CAMPAIGN_KEYS)


def due_upsells(
    db,
    now: datetime | None = None,
    limit: int | None = None,
    user_id: int | None = None,
) -> list[UpsellCandidate]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=config.UPSELL_INTERVAL_DAYS)
    active_subscription = exists().where(
        Subscription.user_id == User.id,
        Subscription.expires_at > now,
    )
    query = db.query(User).filter(
        User.language.isnot(None),
        User.created_at <= cutoff,
        (User.upsell_last_sent_at.is_(None)) | (User.upsell_last_sent_at <= cutoff),
        User.user_channels.any(UserChannel.is_active.is_(True)),
        ~active_subscription,
    )
    if config.ADMIN_IDS:
        query = query.filter(User.telegram_id.notin_(config.ADMIN_IDS))
    if user_id is not None:
        query = query.filter(User.id == user_id)
    rows = query.order_by(User.id).limit(limit or config.UPSELL_BATCH_SIZE).all()
    return [
        UpsellCandidate(
            user_id=user.id,
            telegram_id=user.telegram_id,
            lang=lang_of(user),
            trial_used=user.trial_used,
            variant=_variant(user.id, now),
        )
        for user in rows
    ]


def render_upsell(candidate: UpsellCandidate) -> str:
    return t(
        CAMPAIGN_KEYS[candidate.variant],
        candidate.lang,
        basic_price=price_label("basic"),
        pro_price=price_label("pro"),
        basic_limit=config.CHANNEL_LIMIT_BASIC,
        trial_days=config.TRIAL_DAYS,
    )
```

- [ ] **Step 4: Run eligibility tests and verify GREEN**

Run:

```bash
venv/bin/python -m pytest tests/test_upsell.py -q
```

Expected: the three eligibility tests pass.

- [ ] **Step 5: Write failing localization and keyboard tests**

Append to `tests/test_upsell.py`:

```python
from src.bot import keyboards
from src.bot.i18n import T
from src.services.upsell import CAMPAIGN_KEYS, UpsellCandidate, render_upsell


@pytest.mark.parametrize("lang", ["ru", "en", "es"])
@pytest.mark.parametrize("variant", [0, 1, 2])
def test_every_campaign_renders_in_every_language(lang, variant):
    if CAMPAIGN_KEYS[variant] not in T:
        pytest.fail(f"missing translation key: {CAMPAIGN_KEYS[variant]}")
    candidate = UpsellCandidate(1, 1, lang, False, variant)
    text = render_upsell(candidate)
    assert text
    assert "{" not in text
    assert "Basic" in text or "Pro" in text


def test_trial_keyboard_adds_trial_before_monthly_plans():
    assert hasattr(keyboards, "upsell_keyboard")
    rows = keyboards.upsell_keyboard(trial_used=False, lang="ru").inline_keyboard
    assert rows[0][0].callback_data == "start_trial"
    assert [row[0].callback_data for row in rows[1:]] == [
        "subscribe:basic", "subscribe:pro",
    ]


def test_used_trial_keyboard_has_only_monthly_plans():
    assert hasattr(keyboards, "upsell_keyboard")
    rows = keyboards.upsell_keyboard(trial_used=True, lang="en").inline_keyboard
    assert [row[0].callback_data for row in rows] == [
        "subscribe:basic", "subscribe:pro",
    ]
```

- [ ] **Step 6: Run rendering tests and verify RED**

Run:

```bash
venv/bin/python -m pytest tests/test_upsell.py -q
```

Expected: FAIL because translation keys and `upsell_keyboard` do not exist.

- [ ] **Step 7: Add the keyboard and all nine localized campaign texts**

Add `upsell_keyboard()` to `src/bot/keyboards.py`:

```python
def upsell_keyboard(trial_used: bool, lang: str = "ru") -> InlineKeyboardMarkup:
    from src.bot.payments import price_label
    rows = []
    if not trial_used:
        rows.append([
            InlineKeyboardButton(t("kb_trial", lang), callback_data="start_trial")
        ])
    per_month = t("kb_per_month", lang)
    rows.extend([
        [InlineKeyboardButton(
            f"⭐ Basic — {price_label('basic')} {per_month}",
            callback_data="subscribe:basic",
        )],
        [InlineKeyboardButton(
            f"💎 Pro — {price_label('pro')} {per_month}",
            callback_data="subscribe:pro",
        )],
    ])
    return InlineKeyboardMarkup(rows)
```

Add `upsell_summary`, `upsell_digest`, and `upsell_capacity` dictionaries to
`src/bot/i18n.py`. Use these exact Russian meanings and equivalent concise
English and Spanish versions:

```python
"upsell_summary": {
    "ru": "📝 <b>Не успеваете читать длинные посты?</b>\n\nBasic сделает краткое саммари по запросу — основные мысли без лишнего текста.\n\n⭐ Basic: <b>{basic_price}</b>",
    "en": "📝 <b>No time for long posts?</b>\n\nBasic creates an on-demand summary with the key ideas only.\n\n⭐ Basic: <b>{basic_price}</b>",
    "es": "📝 <b>¿No tienes tiempo para posts largos?</b>\n\nBasic crea un resumen bajo demanda con las ideas principales.\n\n⭐ Basic: <b>{basic_price}</b>",
},
"upsell_digest": {
    "ru": "📰 <b>Читайте каналы один раз в день</b>\n\nPro собирает посты в цельный AI-дайджест и умеет автоматически сокращать новые публикации.\n\n💎 Pro: <b>{pro_price}</b>",
    "en": "📰 <b>Read your channels once a day</b>\n\nPro turns posts into one AI digest and can summarize new publications automatically.\n\n💎 Pro: <b>{pro_price}</b>",
    "es": "📰 <b>Lee tus canales una vez al día</b>\n\nPro reúne los posts en un boletín AI y puede resumir nuevas publicaciones automáticamente.\n\n💎 Pro: <b>{pro_price}</b>",
},
"upsell_capacity": {
    "ru": "📢 <b>Добавьте больше важных источников</b>\n\nBasic поддерживает до {basic_limit} каналов, а Pro — без ограничений.\n\n⭐ {basic_price} · 💎 {pro_price}",
    "en": "📢 <b>Add more important sources</b>\n\nBasic supports up to {basic_limit} channels; Pro has no channel limit.\n\n⭐ {basic_price} · 💎 {pro_price}",
    "es": "📢 <b>Añade más fuentes importantes</b>\n\nBasic admite hasta {basic_limit} canales; Pro no tiene límite.\n\n⭐ {basic_price} · 💎 {pro_price}",
},
```

- [ ] **Step 8: Run all upsell tests and verify GREEN**

Run:

```bash
venv/bin/python -m pytest tests/test_upsell.py -q
```

Expected: all eligibility, localization, and keyboard tests pass.

- [ ] **Step 9: Commit the selection and presentation slice**

```bash
git add src/services/upsell.py src/bot/i18n.py src/bot/keyboards.py tests/test_upsell.py tests/conftest.py
git commit -m "Add localized Free user upsell campaigns"
```

### Task 3: Send offers safely and schedule bounded hourly passes

**Files:**
- Modify: `tests/test_upsell.py`
- Modify: `src/services/upsell.py`
- Modify: `src/services/metrics.py:20-40`
- Modify: `src/userbot/monitor.py:895-925,1240-1285`

- [ ] **Step 1: Write failing success, retry, and revalidation tests**

Append async tests to `tests/test_upsell.py` using an inline `to_thread` fixture:

```python
from unittest.mock import AsyncMock, MagicMock

from src.services import upsell as upsell_module


@pytest.fixture
def inline_to_thread(monkeypatch):
    async def inline(func, *args):
        return func(*args)
    monkeypatch.setattr("src.services.upsell.asyncio.to_thread", inline)


async def test_successful_send_records_cooldown(db, monkeypatch, inline_to_thread):
    now = datetime.now(timezone.utc)
    user = create_user(
        db, telegram_id=9620, language="ru",
        created_at=now - timedelta(days=10),
    )
    _active_channel(db, user, "send_success")
    app = MagicMock()
    app.bot.send_message = AsyncMock()
    monkeypatch.setattr("src.bot.app.ptb_app", app)

    assert hasattr(upsell_module, "send_due_upsells")
    assert await upsell_module.send_due_upsells() >= 1
    db.refresh(user)
    assert user.upsell_last_sent_at is not None


async def test_failed_send_does_not_consume_cooldown(db, monkeypatch, inline_to_thread):
    now = datetime.now(timezone.utc)
    user = create_user(
        db, telegram_id=9621, language="ru",
        created_at=now - timedelta(days=10),
    )
    _active_channel(db, user, "send_failure")
    app = MagicMock()
    app.bot.send_message = AsyncMock(side_effect=RuntimeError("Telegram down"))
    monkeypatch.setattr("src.bot.app.ptb_app", app)

    assert hasattr(upsell_module, "send_due_upsells")
    await upsell_module.send_due_upsells()
    db.refresh(user)
    assert user.upsell_last_sent_at is None


async def test_candidate_that_subscribed_before_send_is_skipped(
    db, monkeypatch, inline_to_thread,
):
    now = datetime.now(timezone.utc)
    user = create_user(
        db, telegram_id=9622, language="ru",
        created_at=now - timedelta(days=10),
    )
    _active_channel(db, user, "revalidate")
    app = MagicMock()
    app.bot.send_message = AsyncMock()
    monkeypatch.setattr("src.bot.app.ptb_app", app)
    original_load = __import__(
        "src.services.upsell", fromlist=["_load_due"]
    )._load_due
    candidates = original_load()
    create_subscription(db, user, tier="basic")
    monkeypatch.setattr("src.services.upsell._load_due", lambda: candidates)

    assert hasattr(upsell_module, "send_due_upsells")
    await upsell_module.send_due_upsells()

    assert all(
        call.kwargs["chat_id"] != user.telegram_id
        for call in app.bot.send_message.await_args_list
    )
```

- [ ] **Step 2: Run send tests and verify RED**

Run:

```bash
venv/bin/python -m pytest tests/test_upsell.py -q
```

Expected: FAIL because `send_due_upsells` and cooldown updates are absent.

- [ ] **Step 3: Implement load, revalidation, send, and post-success mark**

Add to `src/services/upsell.py`:

```python
import asyncio
import logging

from src.bot.keyboards import upsell_keyboard
from src.services import metrics

logger = logging.getLogger(__name__)


def _load_due() -> list[UpsellCandidate]:
    with db_session() as db:
        return due_upsells(db)


def _reload_due_candidate(user_id: int) -> UpsellCandidate | None:
    with db_session() as db:
        rows = due_upsells(db, limit=1, user_id=user_id)
        return rows[0] if rows else None


def mark_upsell_sent(telegram_id: int, sent_at: datetime | None = None) -> bool:
    with db_session() as db:
        user = db.query(User).filter_by(telegram_id=telegram_id).first()
        if user is None:
            return False
        user.upsell_last_sent_at = sent_at or datetime.now(timezone.utc)
        db.commit()
        return True


async def send_due_upsells() -> int:
    from src.bot.app import ptb_app
    if ptb_app is None:
        return 0
    candidates = await asyncio.to_thread(_load_due)
    sent = 0
    for selected in candidates:
        candidate = await asyncio.to_thread(_reload_due_candidate, selected.user_id)
        if candidate is None:
            continue
        try:
            await ptb_app.bot.send_message(
                chat_id=candidate.telegram_id,
                text=render_upsell(candidate),
                parse_mode="HTML",
                reply_markup=upsell_keyboard(candidate.trial_used, candidate.lang),
                disable_web_page_preview=True,
            )
        except Exception as exc:
            logger.warning("Upsell to %s failed: %s", candidate.telegram_id, exc)
            continue
        await asyncio.to_thread(mark_upsell_sent, candidate.telegram_id)
        metrics.record(metrics.UPSELL_SENT, "scheduled")
        sent += 1
    return sent
```

Add to `src/services/metrics.py`:

```python
UPSELL_SENT = "upsell_sent"
```

- [ ] **Step 4: Run send tests and verify GREEN**

Run:

```bash
venv/bin/python -m pytest tests/test_upsell.py -q
```

Expected: all upsell tests pass.

- [ ] **Step 5: Write a failing scheduler-loop test**

Append to `tests/test_upsell.py`:

```python
async def test_upsell_loop_runs_sender_before_sleep(monkeypatch):
    from src.userbot import monitor

    sender = AsyncMock(return_value=2)
    monkeypatch.setattr("src.services.upsell.send_due_upsells", sender)

    async def stop_after_first_pass(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(monitor.asyncio, "sleep", stop_after_first_pass)
    with pytest.raises(asyncio.CancelledError):
        await monitor._upsell_loop()
    sender.assert_awaited_once()
```

- [ ] **Step 6: Run the scheduler test and verify RED**

Run:

```bash
venv/bin/python -m pytest tests/test_upsell.py::test_upsell_loop_runs_sender_before_sleep -q
```

Expected: FAIL because `_upsell_loop` does not exist.

- [ ] **Step 7: Add the hourly scheduler and start it with the other tasks**

Add to `src/userbot/monitor.py`:

```python
async def _upsell_loop() -> None:
    from src.services.upsell import send_due_upsells

    while True:
        try:
            sent = await send_due_upsells()
            if sent:
                logger.info("Sent %d paid-feature offer(s)", sent)
        except Exception as exc:
            logger.warning("Upsell pass failed: %s", exc)
        await asyncio.sleep(3600)
```

Start it in `start_userbot()`:

```python
loop.create_task(_upsell_loop())
```

- [ ] **Step 8: Run the focused scheduler and upsell tests**

Run:

```bash
venv/bin/python -m pytest tests/test_upsell.py -q
```

Expected: all tests pass.

- [ ] **Step 9: Commit the scheduled delivery slice**

```bash
git add src/services/upsell.py src/services/metrics.py src/userbot/monitor.py tests/test_upsell.py
git commit -m "Schedule bounded paid feature offers"
```

### Task 4: Share the cooldown with contextual upgrade messages

**Files:**
- Modify: `src/services/upsell.py`
- Modify: `src/bot/handlers/channels.py:50-61,202-207`
- Modify: `src/bot/handlers/ai.py:23-29,47-55,162-171`
- Modify: `src/bot/handlers/buttons.py:20-34`
- Modify: `src/bot/handlers/callbacks.py:160-181`
- Modify: `src/services/subscription_notifier.py:84-135`
- Modify: `tests/test_handlers.py`
- Modify: `tests/test_callbacks.py`
- Modify: `tests/test_subscription_notifier.py`

- [ ] **Step 1: Write failing helper and handler integration tests**

Add to `tests/test_upsell.py`:

```python
from src.services import upsell as upsell_module


async def test_contextual_offer_advances_cooldown(db, inline_to_thread):
    user = create_user(db, telegram_id=9630, language="ru")

    assert hasattr(upsell_module, "mark_contextual_upsell")
    assert await upsell_module.mark_contextual_upsell(user.telegram_id) is True
    db.refresh(user)
    assert user.upsell_last_sent_at is not None
```

Add to the existing add-channel limit test in `tests/test_handlers.py`:

```python
db.refresh(user)
assert user.upsell_last_sent_at is not None
```

Add to the Free summary callback test in `tests/test_callbacks.py`:

```python
db.refresh(user)
assert user.upsell_last_sent_at is not None
```

Extend the expired-notice send test in `tests/test_subscription_notifier.py`:

```python
db.refresh(user)
assert user.upsell_last_sent_at is not None
```

- [ ] **Step 2: Run contextual tests and verify RED**

Run:

```bash
venv/bin/python -m pytest tests/test_upsell.py tests/test_handlers.py tests/test_callbacks.py tests/test_subscription_notifier.py -q
```

Expected: FAIL because successful contextual offers do not update the cooldown.

- [ ] **Step 3: Implement the shared async marker**

Add to `src/services/upsell.py`:

```python
async def mark_contextual_upsell(telegram_id: int) -> bool:
    marked = await asyncio.to_thread(mark_upsell_sent, telegram_id)
    if marked:
        metrics.record(metrics.UPSELL_SENT, "contextual")
    return marked
```

- [ ] **Step 4: Call the marker only after successful contextual sends**

In each Free-only branch that already sends `subscribe_keyboard`, add after the
successful `reply_text` or `send_message`:

```python
from src.services.upsell import mark_contextual_upsell
await mark_contextual_upsell(user.telegram_id)
```

Apply it to:

- the Free channel-limit response and Free AI-filter response in
  `src/bot/handlers/channels.py`;
- Free `/summary` unavailable/saved responses and `/digest` unavailable in
  `src/bot/handlers/ai.py`;
- the Free summary reply-keyboard response in `src/bot/handlers/buttons.py`;
- the Free summary callback response in `src/bot/handlers/callbacks.py`.

In `send_due_notifications()`, mark only a successfully sent expired notice:

```python
await asyncio.to_thread(_mark_sent, notice.subscription_id, notice.kind)
if notice.kind == "expired":
    from src.services.upsell import mark_contextual_upsell
    await mark_contextual_upsell(notice.telegram_id)
```

Do not mark pre-expiry warnings because those users are still paid.

- [ ] **Step 5: Run contextual tests and verify GREEN**

Run:

```bash
venv/bin/python -m pytest tests/test_upsell.py tests/test_handlers.py tests/test_callbacks.py tests/test_subscription_notifier.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit the shared cooldown slice**

```bash
git add src/services/upsell.py src/bot/handlers/channels.py src/bot/handlers/ai.py src/bot/handlers/buttons.py src/bot/handlers/callbacks.py src/services/subscription_notifier.py tests/test_upsell.py tests/test_handlers.py tests/test_callbacks.py tests/test_subscription_notifier.py
git commit -m "Coordinate contextual and scheduled upsells"
```

### Task 5: Report offers, document behavior, verify, and deploy

**Files:**
- Modify: `tests/test_report.py`
- Modify: `src/services/report.py:45-130`
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Write the failing report test**

Add to `tests/test_report.py`:

```python
def test_report_counts_paid_feature_offers(db):
    _event(db, metrics.UPSELL_SENT)

    report = build_report(db)

    assert "Предложений подписки: 1" in report
```

- [ ] **Step 2: Run the report test and verify RED**

Run:

```bash
venv/bin/python -m pytest tests/test_report.py::test_report_counts_paid_feature_offers -q
```

Expected: FAIL because the report has no upsell count.

- [ ] **Step 3: Add the count to the admin report**

In `build_report()` calculate:

```python
upsells_sent = count(metrics.UPSELL_SENT)
```

Add under the user/subscription section:

```python
f"  📣 Предложений подписки: {upsells_sent}",
```

- [ ] **Step 4: Run the report test and verify GREEN**

Run:

```bash
venv/bin/python -m pytest tests/test_report.py::test_report_counts_paid_feature_offers -q
```

Expected: `1 passed`.

- [ ] **Step 5: Document the product behavior and invariants**

Add to `README.md`:

```markdown
### Предложения подписки

Free-пользователи с активными каналами могут получать локализованное
предложение платных функций не чаще одного раза в `UPSELL_INTERVAL_DAYS`.
Новые пользователи получают первое такое сообщение только после этого же
периода. Платные пользователи и администраторы исключены.
```

Add to the delivery/metrics invariants in `CLAUDE.md`:

```markdown
- Плановый upsell отправляется только Free-пользователям с активным каналом,
  не чаще `UPSELL_INTERVAL_DAYS`; дата меняется только после успешного send.
- Любое успешное контекстное предложение подписки продлевает тот же cooldown.
```

- [ ] **Step 6: Run complete verification**

Run:

```bash
venv/bin/python -m pytest tests/ -q -p no:cacheprovider
venv/bin/python -m flake8 src tests main.py auth_userbot.py scripts
git diff --check
```

Expected: all tests pass; flake8 and `git diff --check` produce no errors.

- [ ] **Step 7: Verify the additive migration on a fresh SQLite file**

Run:

```bash
env DATABASE_URL=sqlite:////tmp/telegramwall-upsell-migration.sqlite \
  venv/bin/python -c "from sqlalchemy import inspect; from src.database import init_db, engine; init_db(); assert 'upsell_last_sent_at' in {c['name'] for c in inspect(engine).get_columns('users')}; print('upsell migration OK')"
```

Expected: `upsell migration OK`. Existing warnings from legacy PostgreSQL-only
`CREATE TABLE` fallback statements do not invalidate this column assertion.

- [ ] **Step 8: Commit the report and documentation slice**

```bash
git add src/services/report.py tests/test_report.py README.md CLAUDE.md
git commit -m "Report paid feature offer delivery"
```

- [ ] **Step 9: Push `main` and verify deployment**

```bash
git push origin main
gh run list --repo 0legRogovenko/TelegramWall --branch main --limit 10
gh workflow run healthcheck.yml --repo 0legRogovenko/TelegramWall
```

Verify that CI succeeds, the new Bot run reaches the long-running
`Start bot (polling mode)` step, and the completed healthcheck log contains
`OK — last seen N min ago`.
