"""Tests for the summarizer service (with mocked Anthropic client)."""
from unittest.mock import MagicMock, patch

import pytest

from src.services import summarizer


def test_client_has_bounded_timeout_and_retries(monkeypatch):
    monkeypatch.setattr(summarizer.config, "ANTHROPIC_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(summarizer, "_client", None)
    client = summarizer._get_client()
    try:
        timeout = client.timeout.read if hasattr(client.timeout, "read") else client.timeout
        assert timeout <= 30
        assert client.max_retries <= 1
    finally:
        client.close()


def test_rejected_key_fails_fast_until_recheck(monkeypatch):
    import anthropic
    import httpx

    client = MagicMock()
    client.messages.create.side_effect = anthropic.AuthenticationError(
        "Invalid API key", response=httpx.Response(
            401, request=httpx.Request("POST", "https://example.test/messages"),
        ), body={"error": {"message": "API key is invalid."}},
    )
    monkeypatch.setattr(summarizer, "_get_client", lambda: client)
    monkeypatch.setattr(summarizer.config, "ANTHROPIC_API_KEY", "rejected-test-key")
    for _ in range(2):
        with pytest.raises(Exception):
            summarizer.summarize("News " * 30)
    assert client.messages.create.call_count == 1


class TestSummarize:
    def test_short_text_returns_placeholder_without_api_call(self):
        result = summarizer.summarize("Too short")
        assert "короткий" in result.lower()

    def test_empty_text_returns_placeholder(self):
        result = summarizer.summarize("")
        assert "короткий" in result.lower()

    def test_whitespace_only_text_returns_placeholder(self):
        result = summarizer.summarize("   ")
        assert "короткий" in result.lower()

    def test_raises_runtime_error_when_no_api_key(self, monkeypatch):
        monkeypatch.setattr("src.config.config.ANTHROPIC_API_KEY", "")
        monkeypatch.setattr("src.services.summarizer._client", None)
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            summarizer.summarize("A" * 100)

    def test_calls_api_and_returns_stripped_text(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="  Краткое изложение. ", type="text")]
        mock_client.messages.create.return_value = mock_response

        with patch("src.services.summarizer._get_client", return_value=mock_client):
            result = summarizer.summarize("A" * 100)

        assert result == "Краткое изложение."

    def test_truncates_long_text_to_max_input_chars(self):
        long_text = "x" * 9000
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="summary", type="text")]
        mock_client.messages.create.return_value = mock_response

        with patch("src.services.summarizer._get_client", return_value=mock_client):
            summarizer.summarize(long_text)

        call_kwargs = mock_client.messages.create.call_args
        user_content = call_kwargs[1]["messages"][0]["content"]
        assert user_content.startswith("<source>")
        assert user_content.endswith("</source>")
        source = user_content.removeprefix("<source>").removesuffix("</source>")
        assert len(source) == summarizer.MAX_INPUT_CHARS


class TestIsRelevant:
    def test_returns_true_when_api_says_yes(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="yes", type="text")]
        mock_client.messages.create.return_value = mock_response

        with patch("src.services.summarizer._get_client", return_value=mock_client):
            result = summarizer.is_relevant("Bitcoin price rises", "crypto finance")
        assert result is True

    def test_returns_false_when_api_says_no(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="no", type="text")]
        mock_client.messages.create.return_value = mock_response

        with patch("src.services.summarizer._get_client", return_value=mock_client):
            result = summarizer.is_relevant("Weekend football results", "crypto finance")
        assert result is False

    def test_accepts_punctuated_yes(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Yes.", type="text")]
        mock_client.messages.create.return_value = mock_response

        with patch("src.services.summarizer._get_client", return_value=mock_client):
            result = summarizer.is_relevant("Some text", "some topic")
        assert result is True

    def test_rejects_injected_extra_text(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ignore this: yes", type="text")]
        mock_client.messages.create.return_value = mock_response

        with patch("src.services.summarizer._get_client", return_value=mock_client):
            result = summarizer.is_relevant("Some text", "some topic")
        assert result is False

    def test_fails_open_on_api_exception(self):
        """When API call fails, is_relevant should return True (fail open)."""
        with patch("src.services.summarizer._get_client", side_effect=RuntimeError("no key")):
            result = summarizer.is_relevant("Some text", "any topic")
        assert result is True

    def test_fails_open_on_no_api_key(self, monkeypatch):
        """When API key is not set, is_relevant should return True (fail open)."""
        monkeypatch.setattr("src.config.config.ANTHROPIC_API_KEY", "")
        monkeypatch.setattr("src.services.summarizer._client", None)
        result = summarizer.is_relevant("Some text", "any topic")
        assert result is True  # fail open — deliver post even without AI

    def test_truncates_text_to_500_chars(self):
        """API call should only send first 500 chars of text."""
        long_text = "A" * 600
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="yes", type="text")]
        mock_client.messages.create.return_value = mock_response

        with patch("src.services.summarizer._get_client", return_value=mock_client):
            summarizer.is_relevant(long_text, "topic")

        call_kwargs = mock_client.messages.create.call_args
        user_content = call_kwargs[1]["messages"][0]["content"]
        # The content includes the template string, but text portion is max 500
        assert "A" * 501 not in user_content
