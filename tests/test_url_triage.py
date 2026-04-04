"""Tests for backend.ingestion.url_triage module."""

import json
import pytest
from unittest.mock import MagicMock, patch

from backend.ingestion.url_triage import (
    triage_telegram_urls,
    _strip_code_fences,
)


def _make_llm_response(content: str):
    """Create a mock LLM response object."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


class TestStripCodeFences:
    def test_strips_json_fence(self):
        assert _strip_code_fences('```json\n["a"]\n```') == '["a"]'

    def test_strips_plain_fence(self):
        assert _strip_code_fences('```\n{"x": 1}\n```') == '{"x": 1}'

    def test_no_fence(self):
        assert _strip_code_fences('["a"]') == '["a"]'


class TestTriageTelegramUrls:
    @pytest.mark.asyncio
    async def test_identifies_receipt_url(self, db_path):
        """LLM correctly identifies a receipt URL from a mix."""
        urls = [
            "https://store.example.com/receipt/12345",
            "https://twitter.com/user/status/999",
            "https://tracking.ups.com/pkg/abc",
        ]
        llm_response = _make_llm_response(
            json.dumps(["https://store.example.com/receipt/12345"])
        )

        with patch("backend.ingestion.url_triage.litellm_completion", return_value=llm_response) as mock_llm, \
             patch("backend.ingestion.url_triage.get_setting", side_effect=lambda k: "gpt-4o" if k == "llm_model" else "test-key"):
            result = await triage_telegram_urls("Here's my receipt", urls)

        assert result == ["https://store.example.com/receipt/12345"]
        mock_llm.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_on_llm_failure(self, db_path):
        """Returns all URLs when LLM call fails."""
        urls = ["https://a.com", "https://b.com"]

        with patch("backend.ingestion.url_triage.litellm_completion", side_effect=Exception("API error")), \
             patch("backend.ingestion.url_triage.get_setting", side_effect=lambda k: "gpt-4o" if k == "llm_model" else "test-key"):
            result = await triage_telegram_urls("some text", urls)

        assert result == urls

    @pytest.mark.asyncio
    async def test_fallback_on_missing_config(self, db_path):
        """Returns all URLs when LLM is not configured."""
        urls = ["https://a.com"]

        with patch("backend.ingestion.url_triage.get_setting", return_value=None):
            result = await triage_telegram_urls("text", urls)

        assert result == urls

    @pytest.mark.asyncio
    async def test_empty_urls(self, db_path):
        """Returns empty list for empty input."""
        result = await triage_telegram_urls("text", [])
        assert result == []

    @pytest.mark.asyncio
    async def test_filters_urls_not_in_input(self, db_path):
        """LLM-returned URLs not in original list are filtered out."""
        urls = ["https://a.com"]
        llm_response = _make_llm_response(
            json.dumps(["https://a.com", "https://hallucinated.com"])
        )

        with patch("backend.ingestion.url_triage.litellm_completion", return_value=llm_response), \
             patch("backend.ingestion.url_triage.get_setting", side_effect=lambda k: "gpt-4o" if k == "llm_model" else "test-key"):
            result = await triage_telegram_urls("text", urls)

        assert result == ["https://a.com"]

    @pytest.mark.asyncio
    async def test_strips_code_fences_from_response(self, db_path):
        """Handles LLM response wrapped in markdown code fences."""
        urls = ["https://invoice.example.com/dl/789"]
        llm_response = _make_llm_response(
            '```json\n["https://invoice.example.com/dl/789"]\n```'
        )

        with patch("backend.ingestion.url_triage.litellm_completion", return_value=llm_response), \
             patch("backend.ingestion.url_triage.get_setting", side_effect=lambda k: "gpt-4o" if k == "llm_model" else "test-key"):
            result = await triage_telegram_urls("Invoice link", urls)

        assert result == ["https://invoice.example.com/dl/789"]


