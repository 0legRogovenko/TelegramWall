import anthropic

from src.config import config

_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

SYSTEM_PROMPT = (
    "Ты помощник для краткого изложения текстов. "
    "Создавай лаконичное саммари на русском языке — 3-5 предложений. "
    "Выдели ключевую мысль и самое важное. "
    "Не добавляй вводных фраз типа 'В этом тексте...' или 'Саммари:'."
)


def summarize(text: str) -> str:
    """Generate a concise summary for the given text using Claude."""
    if not text or len(text.strip()) < 50:
        return "Текст слишком короткий для саммари."

    message = _client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text[:8000]}],
    )
    return message.content[0].text.strip()
