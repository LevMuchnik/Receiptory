"""Tests for backend.ingestion.url_triage module."""

import json
import threading

import litellm
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

        with patch("backend.processing.extract.litellm_completion", return_value=llm_response) as mock_llm, \
             patch("backend.ingestion.url_triage.resolve_llm_api_key", return_value="test-key"), \
             patch("backend.ingestion.url_triage.get_setting", side_effect=_stub_settings):
            result = await triage_telegram_urls("Here's my receipt", urls)

        assert result == ["https://store.example.com/receipt/12345"]
        mock_llm.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_on_llm_failure(self, db_path):
        """Returns all URLs when LLM call fails."""
        urls = ["https://a.com", "https://b.com"]

        with patch("backend.processing.extract.litellm_completion", side_effect=Exception("API error")), \
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

        # The key must resolve, or the "not configured" early exit returns every
        # URL and this passes without the filter ever running.
        with patch("backend.processing.extract.litellm_completion", return_value=llm_response) as mock_llm, \
             patch("backend.ingestion.url_triage.resolve_llm_api_key", return_value="test-key"), \
             patch("backend.ingestion.url_triage.get_setting", side_effect=_stub_settings):
            result = await triage_telegram_urls("text", urls)

        assert result == ["https://a.com"]
        mock_llm.assert_called_once()

    @pytest.mark.asyncio
    async def test_strips_code_fences_from_response(self, db_path):
        """Handles LLM response wrapped in markdown code fences."""
        urls = ["https://invoice.example.com/dl/789"]
        llm_response = _make_llm_response(
            '```json\n["https://invoice.example.com/dl/789"]\n```'
        )

        with patch("backend.processing.extract.litellm_completion", return_value=llm_response) as mock_llm, \
             patch("backend.ingestion.url_triage.resolve_llm_api_key", return_value="test-key"), \
             patch("backend.ingestion.url_triage.get_setting", side_effect=_stub_settings):
            result = await triage_telegram_urls("Invoice link", urls)

        assert result == ["https://invoice.example.com/dl/789"]
        mock_llm.assert_called_once()


class TestTemperatureFlowsToLLM:
    """Issue #11: the configured llm_temperature must reach litellm at all 3 triage sites."""

    @pytest.mark.asyncio
    async def test_telegram_passes_temperature(self, db_path):
        llm_response = _make_llm_response(json.dumps([]))
        with patch("backend.processing.extract.litellm_completion", return_value=llm_response) as mock_llm, \
             patch("backend.ingestion.url_triage.resolve_llm_api_key", return_value="test-key"), \
             patch("backend.ingestion.url_triage.get_setting", side_effect=_stub_settings):
            await triage_telegram_urls("text", ["https://a.com"])
        assert mock_llm.call_args.kwargs["temperature"] == 0.5

    @pytest.mark.asyncio
    async def test_email_urls_passes_temperature(self, db_path):
        llm_response = _make_llm_response(json.dumps([]))
        with patch("backend.processing.extract.litellm_completion", return_value=llm_response) as mock_llm, \
             patch("backend.ingestion.url_triage.resolve_llm_api_key", return_value="test-key"), \
             patch("backend.ingestion.url_triage.get_setting", side_effect=_stub_settings):
            await triage_email_urls("s@x.com", "subject", "body", ["https://a.com"])
        assert mock_llm.call_args.kwargs["temperature"] == 0.5

    @pytest.mark.asyncio
    async def test_classify_documents_passes_temperature(self, db_path):
        llm_response = _make_llm_response(json.dumps([]))
        docs = [ClassificationDocument(identifier="a.pdf", source="attachment", first_page_image=_PNG_1PX)]
        with patch("backend.processing.extract.litellm_completion", return_value=llm_response) as mock_llm, \
             patch("backend.ingestion.url_triage.resolve_llm_api_key", return_value="test-key"), \
             patch("backend.ingestion.url_triage.get_setting", side_effect=_stub_settings):
            await classify_email_documents("s@x.com", "subject", "body", docs)
        assert mock_llm.call_args.kwargs["temperature"] == 0.5


# One behaviour table, run against all three triage calls. Before #67 only the
# Telegram call had behaviour tests; the email-URL and document-classification
# calls were covered by the temperature test alone, and all three now share one
# helper, so a bug there would reach the untested two silently.
_OFFERED = ["https://a.example/receipt", "https://b.example/track", "https://c.example/invoice"]

_CALLERS = {
    "telegram": ("urls", lambda offered: triage_telegram_urls("text", offered)),
    "email": ("urls", lambda offered: triage_email_urls("s@x.com", "subject", "body", offered)),
    "classify": ("identifiers", lambda offered: classify_email_documents(
        "s@x.com", "subject", "body",
        [ClassificationDocument(identifier=o, source="attachment", first_page_image=_PNG_1PX) for o in offered])),
}


def _settings(json_mode=True):
    values = {"llm_model": "gpt-4o", "llm_temperature": 0.5, "llm_reasoning_effort": "none", "llm_json_mode": json_mode}
    return lambda key: values.get(key)


async def _triage(caller, reply=None, *, raises=None, settings=None, api_key="test-key"):
    """Run one triage call with the LLM stubbed; returns (result, llm mock)."""
    _key, call = _CALLERS[caller]
    llm_kwargs = {"side_effect": raises} if raises else {"return_value": _make_llm_response(reply)}
    with patch("backend.processing.extract.litellm_completion", **llm_kwargs) as mock_llm, \
         patch("backend.ingestion.url_triage.resolve_llm_api_key", return_value=api_key), \
         patch("backend.ingestion.url_triage.get_setting", side_effect=settings or _settings()), \
         patch("litellm.supports_response_schema", return_value=True):
        result = await call(list(_OFFERED))
    return result, mock_llm


@pytest.mark.parametrize("caller", list(_CALLERS))
class TestTriageSelection:
    async def test_wrapped_reply_is_filtered_to_what_was_offered(self, caller, db_path):
        key, _ = _CALLERS[caller]
        result, mock_llm = await _triage(caller, json.dumps({key: [_OFFERED[2], "https://invented.example"]}))
        assert result == [_OFFERED[2]]
        mock_llm.assert_called_once()

    async def test_bare_list_from_a_model_that_ignored_the_schema_still_triages(self, caller, db_path):
        result, mock_llm = await _triage(caller, json.dumps([_OFFERED[0]]))
        assert result == [_OFFERED[0]]
        mock_llm.assert_called_once()

    async def test_fenced_wrapped_reply_is_parsed(self, caller, db_path):
        key, _ = _CALLERS[caller]
        result, _ = await _triage(caller, f"```json\n{json.dumps({key: [_OFFERED[1]]})}\n```")
        assert result == [_OFFERED[1]]

    async def test_an_empty_selection_keeps_nothing(self, caller, db_path):
        key, _ = _CALLERS[caller]
        result, _ = await _triage(caller, json.dumps({key: []}))
        assert result == []

    @pytest.mark.parametrize("reply", ['{"wrong_key": []}', "42", '"a string"', "not json at all"])
    async def test_an_unusable_reply_keeps_everything(self, caller, reply, db_path):
        """Decision 2A: a junk document is visible, a dropped receipt is not."""
        result, mock_llm = await _triage(caller, reply)
        assert result == _OFFERED
        mock_llm.assert_called_once()

    async def test_a_failed_call_keeps_everything(self, caller, db_path):
        result, _ = await _triage(caller, raises=Exception("API error"))
        assert result == _OFFERED

    async def test_an_unavailable_database_keeps_everything(self, caller, db_path):
        """The Telegram call used to read its settings outside any guard, so a
        RuntimeError from get_setting escaped to the ingester."""
        def unavailable(_key):
            raise RuntimeError("Database not initialized")
        result, mock_llm = await _triage(caller, "[]", settings=unavailable)
        assert result == _OFFERED
        mock_llm.assert_not_called()

    async def test_no_api_key_keeps_everything_without_calling(self, caller, db_path):
        result, mock_llm = await _triage(caller, "[]", api_key="")
        assert result == _OFFERED
        mock_llm.assert_not_called()

    async def test_json_mode_asks_for_the_selection_schema(self, caller, db_path):
        key, _ = _CALLERS[caller]
        _, mock_llm = await _triage(caller, json.dumps({key: []}))
        kwargs = mock_llm.call_args.kwargs
        assert kwargs["response_format"]["type"] == "json_schema"
        assert kwargs["response_format"]["json_schema"]["schema"] == {
            "type": "object",
            "properties": {key: {"type": "array", "items": {"type": "string"}}},
            "required": [key],
            "additionalProperties": False,
        }
        assert kwargs["drop_params"] is True

    async def test_json_mode_off_sends_no_response_format(self, caller, db_path):
        _, mock_llm = await _triage(caller, "[]", settings=_settings(json_mode=False))
        assert "response_format" not in mock_llm.call_args.kwargs


    async def test_a_rejected_schema_retries_once_in_json_object_mode(self, caller, db_path):
        """Decision D1: without this, a model that refuses the schema turns
        triage into keep-everything on every message."""
        key, call = _CALLERS[caller]
        rejected = litellm.BadRequestError(message="schema too complex", model="gpt-4o", llm_provider="openai")
        with patch("backend.processing.extract.litellm_completion", side_effect=[rejected, _make_llm_response(json.dumps({key: [_OFFERED[0]]}))]) as mock_llm, \
             patch("backend.ingestion.url_triage.resolve_llm_api_key", return_value="test-key"), \
             patch("backend.ingestion.url_triage.get_setting", side_effect=_settings()), \
             patch("litellm.supports_response_schema", return_value=True):
            result = await call(list(_OFFERED))
        assert result == [_OFFERED[0]]
        assert [c.kwargs["response_format"]["type"] for c in mock_llm.call_args_list] == ["json_schema", "json_object"]

    async def test_duplicates_and_non_strings_are_dropped(self, caller, db_path):
        key, _ = _CALLERS[caller]
        result, _ = await _triage(caller, json.dumps({key: [_OFFERED[1], _OFFERED[1], {"url": _OFFERED[2]}, 7]}))
        assert result == [_OFFERED[1]]

    async def test_trailing_text_after_the_json_is_ignored(self, caller, db_path):
        """Gemini's issue #10 shape. A strict json.loads calls it 'Extra data'
        and the fallback keeps every link."""
        key, _ = _CALLERS[caller]
        result, _ = await _triage(caller, json.dumps({key: [_OFFERED[2]]}) + "\n\nI picked the invoice link.")
        assert result == [_OFFERED[2]]


    async def test_the_call_has_a_timeout_and_runs_off_the_event_loop(self, caller, db_path):
        """The Telegram bot polls on FastAPI's own event loop: a blocking call
        made inline would stall every request for as long as the LLM took."""
        key, call = _CALLERS[caller]
        loop_thread = threading.get_ident()
        seen = {}

        def reply(**kwargs):
            seen["thread"], seen["timeout"] = threading.get_ident(), kwargs.get("timeout")
            return _make_llm_response(json.dumps({key: [_OFFERED[0]]}))

        with patch("backend.processing.extract.litellm_completion", side_effect=reply), \
             patch("backend.ingestion.url_triage.resolve_llm_api_key", return_value="test-key"), \
             patch("backend.ingestion.url_triage.get_setting", side_effect=_settings()), \
             patch("litellm.supports_response_schema", return_value=True):
            assert await call(list(_OFFERED)) == [_OFFERED[0]]
        assert seen["thread"] != loop_thread
        assert seen["timeout"]
