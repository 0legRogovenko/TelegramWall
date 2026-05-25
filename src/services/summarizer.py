import anthropic

from src.config import config

SYSTEM_PROMPT = (
    "Ты помощник для краткого изложения текстов. "
    "Создавай лаконичное саммари на русском языке — 3-5 предложений. "
    "Выдели ключевую мысль и самое важное. "
    "Не добавляй вводных фраз типа 'В этом тексте...' или 'Саммари:'."
)

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
            model="claude-haiku-4-5-20251001",
            max_tokens=5,
            system="Answer only 'yes' or 'no'. No other text.",
            messages=[{
                "role": "user",
                "content": f"Is this text relevant to: '{filter_prompt}'?\n\n{text[:500]}",
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
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text[:8000]}],
    )
    return message.content[0].text.strip()
