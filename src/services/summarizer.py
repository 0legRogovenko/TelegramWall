import anthropic

from src.config import config

# Precision-tuned for minimal token spend: output tokens cost 5x input,
# so every prompt hard-caps response length and format.
SUMMARY_SYSTEM = {
    "ru": (
        "Сожми пост из Telegram-канала в саммари на русском языке.\n"
        "Формат: 1-3 коротких предложения, не больше 60 слов. "
        "Простой пост — одно предложение.\n"
        "Передай только факты из текста: главную мысль, ключевые цифры и даты, вывод.\n"
        "Запрещено: вступления, своя оценка, эмодзи, markdown, пересказ второстепенных деталей."
    ),
    "en": (
        "Condense a Telegram channel post into a summary written ALWAYS in English — "
        "translate if the post is in another language.\n"
        "Format: 1-3 short sentences, at most 60 words. Simple post — one sentence.\n"
        "Keep only facts from the text: the main point, key numbers and dates, the takeaway.\n"
        "Forbidden: introductions, your own opinion, emoji, markdown, minor details."
    ),
    "es": (
        "Resume un post de un canal de Telegram SIEMPRE en español — "
        "traduce si el post está en otro idioma.\n"
        "Formato: 1-3 frases cortas, máximo 60 palabras. Post simple — una frase.\n"
        "Solo hechos del texto: la idea principal, cifras y fechas clave, la conclusión.\n"
        "Prohibido: introducciones, opinión propia, emojis, markdown, detalles secundarios."
    ),
}

DIGEST_SYSTEM = {
    "ru": (
        "Ты пишешь дайджест по постам из Telegram-каналов.\n"
        "Вход: блоки вида '@имя_канала:' и посты этого канала, каждый со строки '- '.\n"
        "Выход — только сам дайджест, на русском языке. Для каждого канала:\n"
        "первая строка '📢 @имя_канала', затем связный пересказ его главных новостей, "
        "2-4 предложения, только факты и цифры. Блоки разделяй пустой строкой.\n"
        "Никогда не комментируй входные данные и не задавай вопросов.\n"
        "Запрещено: вступление, заключение, оценки, разметка, эмодзи кроме 📢."
    ),
    "en": (
        "You write a digest of Telegram channel posts.\n"
        "Input: blocks of '@channel_name:' followed by that channel's posts, one per '- ' line.\n"
        "Output only the digest itself, ALWAYS in English — translate the source content "
        "if it is in another language. For each channel:\n"
        "first line '📢 @channel_name', then a coherent recap of its main news, "
        "2-4 sentences, facts and numbers only. Separate blocks with a blank line.\n"
        "Never comment on the input data and never ask questions.\n"
        "Forbidden: introduction, conclusion, opinions, markup, emoji except 📢."
    ),
    "es": (
        "Escribes un boletín de posts de canales de Telegram.\n"
        "Entrada: bloques de '@nombre_del_canal:' seguidos de sus posts, uno por línea '- '.\n"
        "Salida: solo el boletín, SIEMPRE en español — traduce el contenido si está "
        "en otro idioma. Para cada canal:\n"
        "primera línea '📢 @nombre_del_canal', luego un repaso coherente de sus noticias "
        "principales, 2-4 frases, solo hechos y cifras. Separa los bloques con línea en blanco.\n"
        "Nunca comentes los datos de entrada ni hagas preguntas.\n"
        "Prohibido: introducción, conclusión, opiniones, formato, emojis excepto 📢."
    ),
}

FILTER_SYSTEM = "Answer only 'yes' or 'no'."

# Telegram caps post text at 4096 chars — anything above is dead headroom
MAX_INPUT_CHARS = 4500
MAX_FILTER_CHARS = 500
MAX_DIGEST_INPUT_CHARS = 12000
MAX_DIGEST_POST_CHARS = 400

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not config.ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY не задан")
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def _text_of(message) -> str:
    """Extract the text block — content may start with a thinking block."""
    return next((b.text for b in message.content if b.type == "text"), "").strip()


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
        return "yes" in _text_of(msg).lower()
    except Exception:
        return True  # fail open — deliver if AI unavailable


def summarize(text: str, lang: str = "ru") -> str:
    if not text or len(text.strip()) < 50:
        placeholders = {
            "ru": "Текст слишком короткий для саммари.",
            "en": "The text is too short to summarize.",
            "es": "El texto es demasiado corto para resumir.",
        }
        return placeholders.get(lang, placeholders["ru"])

    client = _get_client()
    message = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=250,  # hard cost cap; 60 words is ~120 tokens
        thinking={"type": "disabled"},  # no reasoning tokens for summarization
        system=SUMMARY_SYSTEM.get(lang, SUMMARY_SYSTEM["ru"]),
        messages=[{"role": "user", "content": text[:MAX_INPUT_CHARS]}],
    )
    return _text_of(message)


def build_digest(sections: list[tuple[str, list[str]]], lang: str = "ru") -> str:
    """AI-written digest grouped by source.

    sections: [(channel_username, [post_texts])]
    """
    parts = []
    for name, posts in sections:
        joined = "\n".join(f"- {p[:MAX_DIGEST_POST_CHARS]}" for p in posts if p)
        parts.append(f"@{name}:\n{joined}")
    content = "\n\n".join(parts)[:MAX_DIGEST_INPUT_CHARS]

    client = _get_client()
    message = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=700,
        thinking={"type": "disabled"},  # no reasoning tokens for digest writing
        system=DIGEST_SYSTEM.get(lang, DIGEST_SYSTEM["ru"]),
        messages=[{"role": "user", "content": content}],
    )
    return _text_of(message)
