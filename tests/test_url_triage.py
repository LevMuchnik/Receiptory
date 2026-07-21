"""Tests for backend.ingestion.url_triage module."""

import json
import pytest
from unittest.mock import MagicMock, patch

from backend.ingestion.url_triage import (
    triage_telegram_urls,
    triage_email_urls,
    classify_email_documents,
    ClassificationDocument,
    _strip_code_fences,
)

# 1x1 transparent PNG, for classify_email_documents (needs first_page_image bytes).
_PNG_1PX = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _stub_settings(key):
    """get_setting stub returning correctly-typed values (temperature is numeric)."""
    return {
        "llm_model": "gpt-4o",
        "llm_api_key": "test-key",
        "llm_temperature": 0.5,
    }.get(key, "test-key")


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
             patch("backend.ingestion.url_triage.get_setting", side_effect=_stub_settings):
            result = await triage_telegram_urls("Here's my receipt", urls)

        assert result == ["https://store.example.com/receipt/12345"]
        mock_llm.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_on_llm_failure(self, db_path):
        """Returns all URLs when LLM call fails."""
        urls = ["https://a.com", "https://b.com"]

        with patch("backend.ingestion.url_triage.litellm_completion", side_effect=Exception("API error")), \
             patch("backend.ingestion.url_triage.get_setting", side_effect=_stub_settings):
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
             patch("backend.ingestion.url_triage.get_setting", side_effect=_stub_settings):
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
             patch("backend.ingestion.url_triage.get_setting", side_effect=_stub_settings):
            result = await triage_telegram_urls("Invoice link", urls)

        assert result == ["https://invoice.example.com/dl/789"]


class TestTemperatureFlowsToLLM:
    """Issue #11: the configured llm_temperature must reach litellm at all 3 triage sites."""

    @pytest.mark.asyncio
    async def test_telegram_passes_temperature(self, db_path):
        llm_response = _make_llm_response(json.dumps([]))
        with patch("backend.ingestion.url_triage.litellm_completion", return_value=llm_response) as mock_llm, \
             patch("backend.ingestion.url_triage.get_setting", side_effect=_stub_settings):
            await triage_telegram_urls("text", ["https://a.com"])
        assert mock_llm.call_args.kwargs["temperature"] == 0.5

    @pytest.mark.asyncio
    async def test_email_urls_passes_temperature(self, db_path):
        llm_response = _make_llm_response(json.dumps([]))
        with patch("backend.ingestion.url_triage.litellm_completion", return_value=llm_response) as mock_llm, \
             patch("backend.ingestion.url_triage.get_setting", side_effect=_stub_settings):
            await triage_email_urls("s@x.com", "subject", "body", ["https://a.com"])
        assert mock_llm.call_args.kwargs["temperature"] == 0.5

    @pytest.mark.asyncio
    async def test_classify_documents_passes_temperature(self, db_path):
        llm_response = _make_llm_response(json.dumps([]))
        docs = [ClassificationDocument(identifier="a.pdf", source="attachment", first_page_image=_PNG_1PX)]
        with patch("backend.ingestion.url_triage.litellm_completion", return_value=llm_response) as mock_llm, \
             patch("backend.ingestion.url_triage.get_setting", side_effect=_stub_settings):
            await classify_email_documents("s@x.com", "subject", "body", docs)
        assert mock_llm.call_args.kwargs["temperature"] == 0.5


