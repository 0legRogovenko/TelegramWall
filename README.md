# TelegramWall

[![CI](https://github.com/0legRogovenko/TelegramWall/actions/workflows/ci.yml/badge.svg)](https://github.com/0legRogovenko/TelegramWall/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![License: AGPL v3](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
[![Bot](https://img.shields.io/badge/Telegram-%40tgwallbot-2AABEE?logo=telegram&logoColor=white)](https://t.me/tgwallbot)

TelegramWall собирает новые публикации из выбранных публичных Telegram-каналов
и доставляет их в один личный чат. Бот сохраняет медиа, умеет создавать
AI-саммари и дайджесты и работает на русском, английском и испанском языках.

Попробовать: [@tgwallbot](https://t.me/tgwallbot). На старте бот попросит
выбрать язык; банковская карта для Free-тарифа не нужна.

## Возможности и тарифы

| Возможность | Free | Basic | Pro |
|---|:---:|:---:|:---:|
| Активные каналы | до 3 | до 10 | без лимита |
| Текст, фото, видео, аудио и документы | ✅ | ✅ | ✅ |
| Саммари по запросу | — | ✅ | ✅ |
| AI-фильтр по теме | — | ✅ | ✅ |
| Авто-саммари с сохранением медиа | — | — | ✅ |
| AI-дайджест по выбранным источникам | — | — | ✅ |

По умолчанию месячные тарифы стоят 149 ⭐ для Basic и 499 ⭐ для Pro.
При подключённой ЮKassa используются цены 199 ₽ и 499 ₽. Годовые тарифы,
лимиты и цены настраиваются через переменные окружения.

- Однократный пробный период даёт Pro на 3 дня.
- Реферальная ссылка даёт пригласившему и новому пользователю по 3 дня Basic.
- Администраторы из `TELEGRAM_ADMIN_IDS` получают Pro без ограничения срока.
- Free-пользователям бот может показывать ненавязчивые предложения платных
  функций с общим cooldown, чтобы сообщения не дублировались.

## Команды

Командное меню персонализируется по языку и тарифу: Free-пользователь не видит
платные команды, хотя прямой вызов корректно покажет предложение подписки.

| Доступ | Команда | Назначение |
|---|---|---|
| Все | `/start` | Запустить бота или открыть главное меню |
| Все | `/channels` | Показать каналы, включить/выключить или удалить их |
| Все | `/add_channel @username` | Добавить публичный канал |
| Все | `/remove_channel @username` | Удалить канал |
| Все | `/status` | Показать тариф, лимиты и срок подписки |
| Все | `/subscribe` | Открыть тарифы и оплату |
| Все | `/trial` | Активировать однократный Pro-триал |
| Все | `/stats` | Показать пользовательскую статистику |
| Все | `/refer` | Получить реферальную ссылку |
| Все | `/language` | Сменить язык интерфейса |
| Все | `/help` | Показать справку |
| Basic/Pro | `/summary ID`, `/summary_ID` | Создать саммари доступного поста |
| Basic/Pro | `/filter @канал тема` | Оставлять только релевантные теме посты |
| Basic/Pro | `/filter_@канал` | Открыть фильтр выбранного канала |
| Pro | `/digest` | Выбрать источники и режим AI-дайджеста |
| Pro | `/autosummary` | Совместимый алиас настроек дайджеста/авто-саммари |
| Админ | `/admin` | Открыть продуктовую статистику |
| Админ | `/report` | Получить операционный отчёт за сутки |
| Админ | `/block @канал [причина]` | Запретить источник |
| Админ | `/unblock @канал` | Снять оперативную блокировку |

Ввод `https://t.me/channel_name` автоматически нормализуется в
`@channel_name`. Приватные каналы и invite-ссылки не поддерживаются.

## Как работает доставка

1. Telethon-аккаунт читает публичные каналы без вступления в них.
2. Опрос выполняется раз в минуту и равномерно распределяется между каналами,
   чтобы не создавать всплеск запросов и flood wait.
3. PostgreSQL хранит курсоры каналов, настройки пользователей, посты и
   недоставленную очередь. Поэтому рестарт и новый коммит не требуют повторно
   добавлять каналы.
4. Каждый пост отправляется отдельным сообщением от PTB-бота, от старых к
   новым. Сводки из нескольких постов вместо отдельных сообщений не создаются.
5. Название канала ведёт на оригинал, а под доступным постом есть кнопка
   саммари.

У нового канала бот забирает последние 20 сообщений. После простоя он догоняет
историю от сохранённого курсора, максимум по 60 сообщений на канал за цикл;
остаток остаётся на следующий цикл и не пропускается.

Фото, видео, аудио и документы до `MEDIA_MAX_MB` скачиваются Telethon и
загружаются ботом. Полученный Bot API `file_id` повторно используется для
других подписчиков. Альбом хранится как один пост. Если файл слишком большой,
удалён или недоступен, пользователь получает ссылку на оригинал. При
авто-саммари медиа остаётся, а краткий текст отправляется подписью к нему.

Перед доставкой есть 20-секундный grace period для поздней подписи альбома.
При SIGTERM или ошибке недоставленный хвост сохраняется в `pending_posts` и
повторяется после старта без повторной отправки уже обработанной части.

Посты старше `POST_RETENTION_DAYS` ежедневно удаляются из базы, кроме
записей, ожидающих доставки. Уже отправленные сообщения в Telegram-чате
остаются.

## Архитектура

```text
Публичные Telegram-каналы
          │
          ▼
Telethon reader ──► PostgreSQL/Supabase ──► ordered delivery ──► PTB Bot API
                           │                        │
                           ├── cursors/settings     ├── media upload/cache
                           ├── posts/pending        └── retries after restart
                           └── heartbeat/metrics
                                      │
                                      ├── Anthropic API (только AI-функции)
                                      └── GitHub Actions watchdog
```

Основные модули:

| Путь | Ответственность |
|---|---|
| `main.py` | Один asyncio-процесс: Flask/waitress, PTB и Telethon |
| `src/bot/handlers/` | Команды, кнопки, платежи и локализованные сценарии |
| `src/userbot/monitor.py` | Telethon polling и стабильный runtime-фасад |
| `src/userbot/subscribers.py` | Доступность подписчиков и лимиты каналов |
| `src/userbot/media.py` | Классификация, лимиты и повторное использование медиа |
| `src/userbot/delivery.py` | Сохранение, порядок, retries и pending replay |
| `src/userbot/digests.py` | Генерация, разбиение и расписание дайджестов |
| `src/userbot/maintenance.py` | Heartbeat, отчёты, уведомления и очистка |
| `src/services/summarizer.py` | Anthropic: саммари, дайджест и AI-фильтр |
| `src/models.py` | SQLAlchemy-модели PostgreSQL/SQLite |

Все компоненты бота работают на одном asyncio-потоке. Блокирующие операции
БД и Anthropic на горячих путях выносятся через `asyncio.to_thread`.

## AI и обработка данных

По умолчанию используется `claude-haiku-4-5` с отключённым thinking:

| Задача | Ограничение |
|---|---|
| Саммари | до 4500 символов входа, до 250 output-токенов |
| AI-фильтр | до 500 символов поста, ответ `yes`/`no` |
| Дайджест | до 12 000 символов суммарного входа, до 3000 output-токенов |

Промпты отделяют содержимое каналов XML-тегами как недоверенные данные.
Саммари кэшируется в БД, а число новых саммари ограничено
`AI_DAILY_SUMMARY_LIMIT` в сутки. AI-фильтр работает fail-open: при сбое API
пост доставляется, чтобы внешняя модель не могла остановить основную функцию.

В Anthropic уходят тексты публичных постов, username источника и заданная
пользователем тема фильтра. Telegram ID, имя и контакты пользователя кодом не
добавляются. Не вводите персональные или секретные данные в тему фильтра.
Без `ANTHROPIC_API_KEY` обычная доставка работает, но AI-функции недоступны.

Включённый ежедневный дайджест — отдельный режим чтения: мгновенные копии
постов подавляются, материалы за последние 24 часа группируются по выбранным
источникам, а длинный результат разбивается на несколько сообщений.

## Переменные окружения

Полный, синтетический и безопасный шаблон находится в
[.env.example](.env.example). Обязательные секреты для production:

| Переменная | Назначение |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot API token |
| `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_PHONE` | Telethon API |
| `TELEGRAM_SESSION_STRING` | Переносимая авторизованная сессия reader-аккаунта |
| `TELEGRAM_ADMIN_IDS` | Telegram ID администраторов через запятую |
| `DATABASE_URL` | Постоянный PostgreSQL, в production — Supabase Session Pooler |

Опционально:

- `ANTHROPIC_API_KEY` включает AI.
- `YOOKASSA_PROVIDER_TOKEN` переключает оплату со Stars на RUB/ЮKassa.
- `TELEGRAM_WEBHOOK_URL` и `TELEGRAM_WEBHOOK_SECRET` нужны только для
  HTTPS webhook; production workflow использует polling.
- Остальные лимиты, цены, расписания, Flask и blocklist перечислены ровно один
  раз в [.env.example](.env.example).

`HEARTBEAT_STALE_MINUTES` и `REALERT_HOURS` задаются в GitHub
Settings → Secrets and variables → Actions → Variables. Это параметры
отдельного watchdog job, а не окружение процесса бота.

Никогда не коммитьте `.env`, токены, session string или реальные URL с
паролями. Production-значения должны храниться в GitHub Secrets.

## Локальный запуск

Требуется Python 3.12.

```bash
python3.12 -m venv venv
venv/bin/pip install -r requirements-dev.txt
cp .env.example .env
```

Заполните обязательные значения. Для локальной разработки допустим SQLite:

```dotenv
DATABASE_URL=sqlite:////absolute/path/to/TelegramWall/dev.db
TELEGRAM_WEBHOOK_URL=
```

Один раз получите session string:

```bash
venv/bin/python auth_userbot.py
```

Затем перенесите выведенное значение в `.env` и запустите:

```bash
venv/bin/python main.py
```

Не запускайте локальную polling-копию с тем же bot token одновременно с
production: Telegram вернёт `409 Conflict: terminated by other getUpdates`.

## Проверка изменений

Перед каждым коммитом обязательны:

```bash
venv/bin/python -m pytest tests/ -q -p no:cacheprovider
venv/bin/python -m flake8 src tests main.py auth_userbot.py scripts
git diff --check
```

CI повторяет flake8 и pytest для push и pull request в `main`. Тесты используют
общую in-memory SQLite, поэтому фикстуры очищают закоммиченное состояние явно.

## Деплой через GitHub Actions

Текущий production запускается workflow
[Bot](.github/workflows/bot.yml) в polling-режиме:

1. Добавьте обязательные значения из [.env.example](.env.example) в GitHub
   Settings → Secrets and variables → Actions → Secrets.
2. Не задавайте `TELEGRAM_WEBHOOK_URL` в production — пустое значение включает
   polling.
3. Push в `main` автоматически запускает CI и новую копию бота.
4. `concurrency.cancel-in-progress` завершает предыдущую копию, чтобы не было
   двух одновременных `getUpdates`.
5. Cron перезапускает процесс каждые 2 часа; job имеет timeout 350 минут.
   Разница нужна на случай задержанных или пропущенных cron ticks.
6. SIGTERM сохраняет буфер доставки и метрики, а Supabase сохраняет данные
   пользователей, курсоры и pending-очередь между запусками.

Workflow actions закреплены immutable commit SHA и работают с
`permissions: contents: read`. Dependabot проверяет Python-зависимости и
GitHub Actions еженедельно; CODEOWNERS назначает владельца для критичных файлов.

GitHub Actions — текущая, но не гарантированно непрерывная hosting-среда:
scheduled jobs могут опаздывать. Для строгого SLA перенесите тот же polling
процесс на постоянный worker, не меняя PostgreSQL.

## Мониторинг и устранение неполадок

Бот записывает heartbeat каждые 5 минут. Внешний
[Healthcheck](.github/workflows/healthcheck.yml) читает его раз в 30 минут и
уведомляет администраторов, если сигнал устарел. Ежедневный отчёт в
`ADMIN_REPORT_HOUR_UTC` содержит перезапуски, пользователей, посты,
доставку, AI-токены/стоимость и ошибки. `/report` формирует его вручную.

| Симптом | Что проверить |
|---|---|
| `409 Conflict` | Остановить локальную/старую polling-копию; проверить единственный активный Bot run и webhook |
| Stale heartbeat | Запустить `healthcheck.yml`; проверить Bot run, Supabase, `DATABASE_URL` и Telegram secrets |
| Пропадают посты | Проверить active toggle, тарифный лимит, blocklist и Telethon session; дождаться catch-up следующих циклов |
| AI не отвечает | Проверить ключ, баланс, названия моделей и `ERROR_AI` в `/report`; доставка без AI продолжает работать |
| Медиа приходит ссылкой | Проверить `MEDIA_MAX_MB`, доступность исходного сообщения и лимиты Bot API |
| Отчёт не приходит | Проверить `TELEGRAM_ADMIN_IDS`, час UTC и свежий heartbeat |

Ручная проверка production:

```bash
gh workflow run healthcheck.yml --repo 0legRogovenko/TelegramWall
gh run list --repo 0legRogovenko/TelegramWall --limit 10
```

В логе успешного watchdog должен быть `OK — last seen N min ago` либо
однократное сообщение о восстановлении. Логи длительного Bot job становятся
доступны после его завершения или отмены.

## Безопасность и правовые ограничения

- `.env`, Telegram session и ключи исключены из Git; секреты хранятся в
  GitHub Secrets и не должны попадать в логи.
- Webhook-режим проверяет `X-Telegram-Bot-Api-Secret-Token`; polling production
  не открывает webhook для Telegram.
- GitHub Actions использует минимальные read-only permissions и action SHA.
- Контент каналов экранируется перед Telegram HTML; посты считаются
  недоверенными данными и не управляют AI-промптами.
- Статический `BLOCKED_CHANNELS` и команды `/block`/`/unblock` позволяют
  оператору исключать юридически рискованные источники. Код намеренно не
  содержит готового списка: решение принимает оператор.
- Тексты публичных каналов и тема AI-фильтра передаются в Anthropic за пределы
  инфраструктуры проекта. Перед продуктовым запуском оцените требования
  152-ФЗ, трансграничной передачи, авторского права и правил Telegram.
- Бот читает только публичные источники, но публичность не отменяет права
  авторов и ответственность за распространение. Этот раздел не является
  юридической консультацией.

## Лицензия

Проект распространяется по
[GNU Affero General Public License v3.0](LICENSE) © 2026 Олег Роговенко.

Код можно изучать, изменять и запускать. Если изменённая версия доступна
пользователям по сети, AGPL требует предоставить им исходный код этой версии.
Ссылка на репозиторий доступна пользователю в справке бота.

## Автор и контакты

**Олег Роговенко**

- Telegram: [@bapestask8](https://t.me/bapestask8)
- Email: [080806oleg@gmail.com](mailto:080806oleg@gmail.com)
- GitHub: [@0legRogovenko](https://github.com/0legRogovenko)

Для ошибок и предложений откройте
[issue](https://github.com/0legRogovenko/TelegramWall/issues) или напишите автору.
