import anthropic

from src.config import config

# Precision-tuned for minimal token spend: output tokens cost 5x input,
# so the prompt hard-caps response length and format.
SYSTEM_PROMPT = (
    "Сожми пост из Telegram-канала в саммари на русском языке.\n"
    "Формат: 1-3 коротких предложения, не больше 60 слов. "
    "Простой пост — одно предложение.\n"
    "Передай только факты из текста: главную мысль, ключевые цифры и даты, вывод.\n"
    "Запрещено: вступления, своя оценка, эмодзи, markdown, пересказ второстепенных деталей."
)

FILTER_SYSTEM = "Answer only 'yes' or 'no'."

# Telegram caps post text at 4096 chars — anything above is dead headroom
MAX_INPUT_CHARS = 4500
MAX_FILTER_CHARS = 500

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not config.ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY не задан")
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def is_relevant(text: str, filter_prompt: str) -> bool:
    """Return True if text matches the user's AI filter description."""
    try:
        client = _get_client()
        msg = client.messages.create(
            model=config.CLAUDE_FILTER_MODEL,
            max_tokens=3,
            system=FILTER_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    f"Topic: {filter_prompt}\n\n"
                    f"Text:\n{text[:MAX_FILTER_CHARS]}\n\n"
                    "Is the text relevant to the topic?"
                ),
            }],
        )
        return "yes" in msg.content[0].text.strip().lower()
    except Exception:
        return True  # fail open — deliver if AI unavailable


def summarize(text: str) -> str:
    if not text or len(text.strip()) < 50:
        return "Текст слишком короткий для саммари."

    client = _get_client()
    message = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=250,  # hard cost cap; 60 words of Russian is ~120 tokens
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text[:MAX_INPUT_CHARS]}],
    )
    return message.content[0].text.strip()
