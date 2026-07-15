# TelegramWall

[![CI](https://github.com/0legRogovenko/TelegramWall/actions/workflows/ci.yml/badge.svg)](https://github.com/0legRogovenko/TelegramWall/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![Bot](https://img.shields.io/badge/Telegram-%40tgwallbot-2AABEE?logo=telegram&logoColor=white)](https://t.me/tgwallbot)

Telegram-бот — агрегатор каналов: собирает новые посты из выбранных каналов и
присылает их в один чат. Умеет AI-саммари, AI-дайджесты и AI-фильтры по теме.
Интерфейс на трёх языках: 🇷🇺 русский, 🇬🇧 английский, 🇪🇸 испанский.

Попробовать: [@tgwallbot](https://t.me/tgwallbot) — 3 канала бесплатно, карта не нужна.

## Возможности

| Функция | Тариф |
|---|---|
| Пересылка новых постов (до 3 каналов) | Free |
| До 10 каналов + саммари по запросу (`/summary_ID`, кнопка под постом) | ⭐ Basic |
| AI-фильтр — только посты по заданной теме (`/filter @канал тема`) | ⭐ Basic |
| ∞ каналов, авто-саммари вместо полных постов, AI-дайджест | 💎 Pro |

- **AI-дайджест** — пользователь галочками выбирает источники, Claude пишет
  цельный текст с разделами по каналам; длинный дайджест приходит несколькими
  сообщениями. Ежедневный автодайджест — в `DIGEST_HOUR_UTC`.
- **Умная доставка** — одиночные посты приходят сразу (название канала —
  ссылка на оригинал, под постом кнопка «📝 Саммари»); пачка постов
  (например, при добавлении канала) приходит одной сводкой, а не 20 сообщениями.
- **База не разрастается** — посты старше `POST_RETENTION_DAYS` (по умолчанию
  3 дня) ежедневно удаляются из БД; уже доставленные сообщения в чатах остаются.
- **Оплата** — Telegram Stars, либо ЮKassa (рубли) при заданном
  `YOOKASSA_PROVIDER_TOKEN`. Триал Pro — 3 дня, рефералка — +3 дня Basic.

## Архитектура

```
main.py                  Flask (webhook/health) + PTB-бот + Telethon-юзербот в одном процессе
src/bot/handlers/        команды и кнопки (пакет по доменам)
src/bot/i18n.py          все тексты на ru/en/es
src/services/summarizer.py  вызовы Claude: саммари, дайджест, фильтр релевантности
src/userbot/monitor.py   опрос каналов, буферизация, доставка, расписания
src/models.py            SQLAlchemy-модели (PostgreSQL / SQLite для dev)
```

Юзербот только читает публичные каналы (не вступает в них). Очередь отложенной
доставки хранится в БД и переживает рестарты.

## AI

| Задача | Модель (по умолчанию) | Параметры |
|---|---|---|
| Саммари поста | `claude-haiku-4-5` (`CLAUDE_MODEL`) | ≤250 токенов, thinking off |
| Дайджест | `claude-haiku-4-5` | ≤1000 токенов, разбивка по темам |
| Фильтр по теме | `claude-haiku-4-5` (`CLAUDE_FILTER_MODEL`) | ответ yes/no, ≤3 токенов |

Саммари кэшируются в БД — повторный запрос бесплатен. При недоступности API
фильтр пропускает посты (fail-open), доставка не ломается.

## Запуск локально

```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env          # заполнить токены
venv/bin/python auth_userbot.py   # один раз: получить TELEGRAM_SESSION_STRING
venv/bin/python main.py       # polling-режим, если TELEGRAM_WEBHOOK_URL не https
```

Тесты и линтер:

```bash
venv/bin/python -m pytest tests/ -q
venv/bin/python -m flake8 src tests main.py
```

## Деплой (GitHub Actions)

Бот работает в GitHub Actions (`.github/workflows/bot.yml`): пуш в `main`
перезапускает его; cron рестартует каждые 5 часов; при остановке буфер
сбрасывается в БД (SIGTERM-хук). CI (`ci.yml`) гоняет flake8 + pytest.

Секреты репозитория: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_API_ID`,
`TELEGRAM_API_HASH`, `TELEGRAM_PHONE`, `TELEGRAM_SESSION_STRING`,
`TELEGRAM_ADMIN_IDS`, `DATABASE_URL` (внешний PostgreSQL, например Supabase),
`ANTHROPIC_API_KEY`, опционально `YOOKASSA_PROVIDER_TOKEN`.

Полный список настроек — в [.env.example](.env.example).

## Админ

`/admin` (для `TELEGRAM_ADMIN_IDS`) — пользователи и языки, подписки и выручка,
посты за 24ч/7д, очередь доставки, топ каналов. Админы получают Pro навсегда.

## Автор и контакты

**Олег Роговенко**

- 💬 Telegram: [@bapestask8](https://t.me/bapestask8)
- 📧 Email: [080806oleg@gmail.com](mailto:080806oleg@gmail.com)
- 🐙 GitHub: [@0legRogovenko](https://github.com/0legRogovenko)

Нашли баг или есть идея — пишите на почту или открывайте
[issue](https://github.com/0legRogovenko/TelegramWall/issues).
