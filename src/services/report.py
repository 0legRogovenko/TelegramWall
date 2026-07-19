"""Daily operational report for admins."""
import html
from datetime import datetime, timedelta, timezone

from sqlalchemy import func as sqlfunc

from src.bot.keyboards import SEP
from src.config import config
from src.models import (
    BotEvent,
    BotHealth,
    Channel,
    PendingPost,
    Subscription,
    User,
    UserChannel,
)
from src.services import metrics


def _ago(ts: datetime | None) -> str:
    if ts is None:
        return "—"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    mins = int((datetime.now(timezone.utc) - ts).total_seconds() // 60)
    if mins < 1:
        return "только что"
    if mins < 60:
        return f"{mins} мин назад"
    hours = mins // 60
    if hours < 24:
        return f"{hours} ч назад"
    return f"{hours // 24} дн назад"


def build_report(db) -> str:
    """Render the 24h ops report (Russian — admins only)."""
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(hours=24)

    def count(event_type: str) -> int:
        return db.query(BotEvent).filter(
            BotEvent.type == event_type, BotEvent.created_at >= day_ago
        ).count()

    health = db.query(BotHealth).filter_by(id=1).first()
    restarts = count(metrics.STARTED)

    # Users
    total_users = db.query(User).count()
    new_users = db.query(User).filter(User.created_at >= day_ago).count()
    active_users = db.query(UserChannel.user_id).filter_by(is_active=True).distinct().count()

    # Content
    posts_saved = count(metrics.POST_SAVED)
    delivered = count(metrics.DELIVERED_POST)
    bursts = count(metrics.DELIVERED_BURST)
    summaries_sent = count(metrics.DELIVERED_SUMMARY)
    pending = db.query(PendingPost).count()
    total_channels = db.query(Channel).count()

    # AI + cost from real token usage
    ai_details = [
        row[0] for row in db.query(BotEvent.detail).filter(
            BotEvent.type.in_([metrics.AI_SUMMARY, metrics.AI_DIGEST, metrics.AI_FILTER]),
            BotEvent.created_at >= day_ago,
        ).all()
    ]
    cost = metrics.ai_cost_usd(ai_details)
    ai_summary = count(metrics.AI_SUMMARY)
    ai_digest = count(metrics.AI_DIGEST)
    ai_filter = count(metrics.AI_FILTER)

    # Errors, grouped. Matched against the known types rather than LIKE
    # "error_%" — in SQL the underscore is a single-character wildcard, so that
    # pattern would also swallow any future type merely starting with "error".
    err_types = [metrics.ERROR_AI, metrics.ERROR_DELIVERY, metrics.ERROR_MEDIA]
    err_rows = (
        db.query(BotEvent.type, sqlfunc.count(BotEvent.id))
        .filter(BotEvent.type.in_(err_types), BotEvent.created_at >= day_ago)
        .group_by(BotEvent.type)
        .all()
    )
    err_total = sum(c for _, c in err_rows)
    last_err = (
        db.query(BotEvent)
        .filter(BotEvent.type.in_(err_types), BotEvent.created_at >= day_ago)
        .order_by(BotEvent.created_at.desc())
        .first()
    )

    # Money
    paid_subs = db.query(Subscription).filter(
        Subscription.expires_at > now, Subscription.stars_paid > 0
    ).count()

    started = health.started_at if health else None
    lines = [
        f"🩺 <b>Отчёт за сутки</b>  <i>{now.strftime('%d.%m.%Y %H:%M')} UTC</i>",
        "",
        "<b>Работа</b>",
        f"  ❤️ Последний сигнал: {_ago(health.last_seen_at if health else None)}",
        f"  🔄 Запусков за сутки: {restarts}",
        f"  ⏱ Текущий процесс запущен: {_ago(started)}",
        "",
        "<b>Пользователи</b>",
        f"  👤 Всего: <b>{total_users}</b>  (+{new_users} за сутки)",
        f"  ✅ С активными каналами: {active_users}",
        f"  💳 Платных подписок: {paid_subs}",
        SEP,
        "<b>Контент</b>",
        f"  📥 Новых постов собрано: <b>{posts_saved}</b>",
        f"  📤 Доставлено: {delivered} поштучно  ·  {bursts} сводок  ·  {summaries_sent} саммари",
        f"  📢 Каналов в базе: {total_channels}  ·  📦 в очереди: {pending}",
        "",
        "<b>AI</b>",
        f"  📝 Саммари: {ai_summary}  ·  📰 Дайджестов: {ai_digest}  ·  🔍 Фильтров: {ai_filter}",
        f"  💰 Потрачено: <b>${cost:.4f}</b>",
    ]

    # The price table is Haiku's. Say so rather than quietly reporting a wrong
    # number if someone points CLAUDE_MODEL somewhere else.
    if not config.CLAUDE_MODEL.startswith("claude-haiku"):
        lines.append(f"  <i>⚠️ сумма посчитана по ценам Haiku, модель: {config.CLAUDE_MODEL}</i>")

    if err_total:
        lines += ["", f"<b>⚠️ Ошибки: {err_total}</b>"]
        for etype, cnt in err_rows:
            lines.append(f"  · {etype.replace('error_', '')}: {cnt}")
        if last_err and last_err.detail:
            # Escaped: this is raw exception text. A 502 from the AI gateway
            # embeds an HTML body, and one stray '<' makes Telegram reject the
            # whole report — losing it precisely on the days it matters most.
            lines.append(f"  <i>Последняя: {html.escape(last_err.detail[:150])}</i>")
    else:
        lines += ["", "✅ <b>Ошибок нет</b>"]

    return "\n".join(lines)


async def send_report_to_admins() -> bool:
    """Send the daily report to every admin. Returns True if sent to anyone."""
    from src.bot.app import ptb_app
    from src.database import get_session

    if ptb_app is None or not config.ADMIN_IDS:
        return False

    db = get_session()
    try:
        text = build_report(db)
    finally:
        db.close()

    sent = False
    for admin_id in config.ADMIN_IDS:
        try:
            await ptb_app.bot.send_message(
                chat_id=admin_id, text=text, parse_mode="HTML",
                disable_web_page_preview=True,
            )
            sent = True
        except Exception:
            pass  # a broken admin chat must not block the others
    return sent
