"""Small shared helpers."""


def plural_posts(n: int) -> str:
    """Russian pluralization for «пост»: 1 пост, 2 поста, 5 постов."""
    if n % 10 == 1 and n % 100 != 11:
        return "пост"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "поста"
    return "постов"
