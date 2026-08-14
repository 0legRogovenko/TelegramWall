from string import Formatter

from src.bot.i18n import DEFAULT_LANG, T, t


def _fields(template: str) -> set[str]:
    return {name for _, name, _, _ in Formatter().parse(template) if name}


def test_every_translation_has_default_and_matching_placeholders():
    for key, translations in T.items():
        assert DEFAULT_LANG in translations, key
        expected = _fields(translations[DEFAULT_LANG])
        for lang, template in translations.items():
            assert _fields(template) == expected, f"{key}:{lang}"


def test_missing_key_falls_back_to_visible_key_name():
    assert t("missing_test_key", "ru") == "missing_test_key"
