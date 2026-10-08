"""Telegram-sized HTML messages without broken tags or character entities."""
import html
import re

MAX_MESSAGE_UNITS = 3900
_TOKENS = re.compile(r'<[^>]*>|&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z]+);|[^<&]+|[<&]')


def visible_units(text: str) -> int:
    """Conservative UTF-16 budget for Telegram text and captions."""
    visible = html.unescape(re.sub(r"<[^>]+>", "", text))
    return len(visible.encode("utf-16-le")) // 2


def split_html(text: str, limit: int = MAX_MESSAGE_UNITS) -> list[str]:
    """Prefer paragraph boundaries; close/reopen formatting at hard splits."""
    if visible_units(text) <= limit:
        return [text]
    chunks = []
    stack: list[tuple[str, str]] = []
    current: list[str] = []
    used = 0

    def flush():
        nonlocal current, used
        if used:
            chunks.append("".join(current) + "".join(
                f"</{name}>" for name, _ in reversed(stack)
            ))
        current = [raw for _, raw in stack]
        used = 0

    def append_visible(raw, units):
        nonlocal used
        if used + units > limit:
            flush()
        current.append(raw)
        used += units

    for index, paragraph in enumerate(text.split("\n\n")):
        if index:
            size = visible_units(paragraph)
            if used and size <= limit and used + 2 + size > limit:
                flush()
            else:
                append_visible("\n\n", 2)
        for raw in _TOKENS.findall(paragraph):
            if raw.startswith("<") and raw.endswith(">"):
                match = re.match(r"</?([\w-]+)", raw)
                if not match:
                    raise ValueError("Invalid Telegram HTML tag")
                tag = match.group(1)
                if raw.startswith("</"):
                    if not stack or stack[-1][0] != tag:
                        raise ValueError("Unbalanced Telegram HTML tags")
                    stack.pop()
                elif not raw.endswith("/>"):
                    stack.append((tag, raw))
                current.append(raw)
            elif raw.startswith("&") and raw.endswith(";"):
                append_visible(raw, len(html.unescape(raw).encode("utf-16-le")) // 2)
            else:
                for char in raw:
                    append_visible(char, 2 if ord(char) > 0xFFFF else 1)
    if used:
        chunks.append("".join(current))
    return chunks


async def send_html(bot, chat_id: int, text: str, reply_markup=None) -> None:
    chunks = split_html(text)
    for index, chunk in enumerate(chunks):
        await bot.send_message(
            chat_id=chat_id, text=chunk, parse_mode="HTML", disable_web_page_preview=True,
            reply_markup=reply_markup if index == len(chunks) - 1 else None,
        )
