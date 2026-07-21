import json
import logging
import pytest
from unittest.mock import patch
from backend.processing.extract import (build_extraction_prompt, parse_llm_response, extract_document, ParseFailure, _MAX_PARSE_RETRIES)
from tests.conftest import SAMPLE_LLM_RESPONSE, mock_llm_response

EXTRACT_LOGGER = "backend.processing.extract"

def test_build_prompt_includes_business_info():
    prompt = build_extraction_prompt(business_names=["Acme Corp", 'אקמה בע"מ'], business_addresses=["123 Main St", "רחוב ראשי 123"], business_tax_ids=["515000000"], expense_categories=[{"name": "office_supplies", "description": "Office equipment and supplies"}, {"name": "travel", "description": "Travel expenses"}], issued_categories=[])
    assert "Acme Corp" in prompt
    assert "515000000" in prompt
    assert "office_supplies" in prompt
    assert "Office equipment and supplies" in prompt

def test_parse_valid_response():
    result = parse_llm_response(SAMPLE_LLM_RESPONSE)
    assert result.vendor_name == "Office Depot"
    assert result.total_amount == 354.51
    assert result.extraction_confidence == 0.95
    assert result.document_type == "expense_receipt"
    assert result.category_name == "office_supplies"
    assert len(result.line_items) == 2

def test_parse_response_with_markdown_fence():
    wrapped = f"```json\n{SAMPLE_LLM_RESPONSE}\n```"
    result = parse_llm_response(wrapped)
    assert result.vendor_name == "Office Depot"

def test_parse_invalid_json():
    with pytest.raises(ValueError, match="Failed to parse"):
        parse_llm_response("this is not json")


# --- Tolerant parse ladder (issue #10) ---

def test_parse_json_with_trailing_junk(caplog):
    # Regression: docs #70 and #215 failed with "Extra data" on exactly this shape.
    junk = "\n\nNote: the totals above were computed from the line items shown."
    with caplog.at_level(logging.WARNING, logger=EXTRACT_LOGGER):
        result = parse_llm_response(SAMPLE_LLM_RESPONSE + junk)
    assert result.vendor_name == "Office Depot"
    assert result.total_amount == 354.51
    assert "trailing data" in caplog.text
    assert "totals above were computed" in caplog.text  # snippet is logged


def test_parse_json_with_trailing_whitespace_no_warning(caplog):
    with caplog.at_level(logging.WARNING, logger=EXTRACT_LOGGER):
        result = parse_llm_response(SAMPLE_LLM_RESPONSE + "\n\n   \n")
    assert result.vendor_name == "Office Depot"
    assert caplog.text == ""


def test_parse_trailing_junk_with_fenced_block_does_not_hijack():
    # A fenced example inside the trailing junk must not replace the real payload.
    junk = '\nFor reference, a minimal example would be: ```json\n{"vendor_name": "WRONG"}\n```'
    result = parse_llm_response(SAMPLE_LLM_RESPONSE + junk)
    assert result.vendor_name == "Office Depot"


def test_parse_fenced_json_with_junk_after_fence(caplog):
    wrapped = f"```json\n{SAMPLE_LLM_RESPONSE}\n```\nThat concludes the extraction."
    with caplog.at_level(logging.WARNING, logger=EXTRACT_LOGGER):
        result = parse_llm_response(wrapped)
    assert result.vendor_name == "Office Depot"
    assert "concludes the extraction" in caplog.text


def test_parse_fenced_json_with_backticks_in_payload(caplog):
    # The old paired-fence regex truncated at the first closing ``` even when it
    # appeared inside a JSON string value (e.g. OCR text of a code-snippet receipt).
    data = json.loads(SAMPLE_LLM_RESPONSE)
    data["raw_extracted_text"] = "Install with:\n```\nnpm install receiptory\n```\nThanks!"
    wrapped = f"```json\n{json.dumps(data)}\n```"
    with caplog.at_level(logging.WARNING, logger=EXTRACT_LOGGER):
        result = parse_llm_response(wrapped)
    assert "npm install receiptory" in result.raw_extracted_text
    assert result.vendor_name == "Office Depot"
    assert "trailing data" not in caplog.text  # bare closing fence is expected, not junk
    assert "markdown fence" in caplog.text  # but the fence deviation itself is logged


def test_parse_leading_prose_salvage(caplog):
    with caplog.at_level(logging.WARNING, logger=EXTRACT_LOGGER):
        result = parse_llm_response("Here is the extracted data:\n" + SAMPLE_LLM_RESPONSE)
    assert result.vendor_name == "Office Depot"
    assert "salvage" in caplog.text


def test_parse_leading_prose_with_decoy_brace():
    prose = "I'll return {fields} as JSON: "
    result = parse_llm_response(prose + SAMPLE_LLM_RESPONSE)
    assert result.vendor_name == "Office Depot"
    assert result.total_amount == 354.51


def test_parse_braces_but_no_valid_json():
    with pytest.raises(ValueError, match="Failed to parse"):
        parse_llm_response("set {x} to {y} please")


def test_parse_non_dict_json_raises_value_error():
    # raw_decode accepts any leading JSON value; a non-dict must not slip
    # through and crash later with AttributeError on data.get().
    with pytest.raises(ValueError, match="Failed to parse"):
        parse_llm_response("2026 was a fine year")
    with pytest.raises(ValueError, match="Failed to parse"):
        parse_llm_response(json.dumps([1, 2, 3]))


def test_parse_empty_and_whitespace_only():
    with pytest.raises(ValueError, match="Failed to parse"):
        parse_llm_response("")
    with pytest.raises(ValueError, match="Failed to parse"):
        parse_llm_response("   \n  ")


def test_parse_fence_with_invalid_content_falls_through_to_salvage(caplog):
    # A fence whose content isn't a JSON object must not end the ladder —
    # a valid object later in the response is still salvaged.
    text = "```\nthis is not json\n```\n" + SAMPLE_LLM_RESPONSE
    with caplog.at_level(logging.WARNING, logger=EXTRACT_LOGGER):
        result = parse_llm_response(text)
    assert result.vendor_name == "Office Depot"
    assert "salvage" in caplog.text


def test_parse_array_wrapped_object_salvages_inner(caplog):
    # Models occasionally wrap the payload in a one-element array; the
    # salvage tier recovers the inner object.
    wrapped = json.dumps([json.loads(SAMPLE_LLM_RESPONSE)])
    with caplog.at_level(logging.WARNING, logger=EXTRACT_LOGGER):
        result = parse_llm_response(wrapped)
    assert result.vendor_name == "Office Depot"
    assert "salvage" in caplog.text


def test_parse_rejects_empty_object():
    # json_object mode guarantees syntax, not shape — {} must fail loudly,
    # not file an all-None record as processed.
    with pytest.raises(ValueError, match="does not match the extraction schema"):
        parse_llm_response("{}")


def test_parse_rejects_line_item_fragment():
    # A response truncated mid-document leaves complete inner line items;
    # {"description", "quantity", "unit_price"} shares only ONE key with the
    # schema and must not be salvaged as the whole document.
    truncated = SAMPLE_LLM_RESPONSE[:SAMPLE_LLM_RESPONSE.index("}") + 1]
    with pytest.raises(ValueError, match="Failed to parse"):
        parse_llm_response(truncated)


def test_parse_rejects_non_finite_and_out_of_range_numerics():
    # Python's json accepts NaN/Infinity constants; NaN defeats comparison
    # gates (NaN < x is False) and sqlite stores it as NULL. Booleans would
    # coerce to 1.0. Out-of-range confidence (e.g. 95 meaning 95%) is unknown.
    data = json.loads(SAMPLE_LLM_RESPONSE)
    data.update({"extraction_confidence": "NaN", "total_amount": "Infinity", "subtotal": True})
    result = parse_llm_response(json.dumps(data))
    assert result.extraction_confidence is None
    assert result.total_amount is None
    assert result.subtotal is None
    data = json.loads(SAMPLE_LLM_RESPONSE)
    data["extraction_confidence"] = 95
    result = parse_llm_response(json.dumps(data))
    assert result.extraction_confidence is None


def test_parse_survives_pathological_nesting():
    # Deeply nested JSON raises RecursionError (not JSONDecodeError) inside
    # the json module — the ladder must degrade to ValueError, not crash.
    with pytest.raises(ValueError, match="Failed to parse"):
        parse_llm_response("[" * 100000)


def test_salvage_attempt_cap():
    # 1000-candidate cap bounds CPU on brace-heavy garbage; just under the cap
    # still salvages, past the cap fails loudly.
    salvageable = "{x} " * 999 + SAMPLE_LLM_RESPONSE
    assert parse_llm_response(salvageable).vendor_name == "Office Depot"
    capped = "{x} " * 1001 + SAMPLE_LLM_RESPONSE
    with pytest.raises(ValueError, match="Failed to parse"):
        parse_llm_response(capped)


def test_salvage_prefers_best_schema_match():
    # A prose-embedded example object with SOME schema keys must not beat the
    # real payload later in the response — most-keys wins, not first-found.
    decoy = '{"vendor_name": "EXAMPLE Inc", "total_amount": 1.0}'
    text = f"Example output: {decoy}\nActual data:\n{SAMPLE_LLM_RESPONSE}"
    result = parse_llm_response(text)
    assert result.vendor_name == "Office Depot"
    assert result.total_amount == 354.51


def test_fenced_decoy_does_not_beat_real_payload():
    # A FENCED example object must compete on schema-key overlap like any
    # other candidate — the fence tier must not short-circuit best-match.
    text = ('Here is the format I will use: ```json\n{"vendor_name": "EXAMPLE Corp", "total_amount": 0.0}\n```\n'
            f"Now the actual extraction:\n{SAMPLE_LLM_RESPONSE}")
    result = parse_llm_response(text)
    assert result.vendor_name == "Office Depot"
    assert result.total_amount == 354.51


def test_parse_coerces_line_item_numerics():
    # NaN/string numbers inside line_items must be sanitized like the scalar
    # money fields — a NaN unit_price serializes as invalid JSON downstream.
    data = json.loads(SAMPLE_LLM_RESPONSE)
    data["line_items"] = [{"description": "Paper", "quantity": "5", "unit_price": "NaN"}]
    result = parse_llm_response(json.dumps(data))
    assert result.line_items[0]["quantity"] == 5.0
    assert result.line_items[0]["unit_price"] is None
    data["line_items"] = "not a list"
    result = parse_llm_response(json.dumps(data))
    assert result.line_items == []


def test_log_sanitization_strips_control_chars(caplog):
    # ANSI escapes and NULs in LLM output must not reach the log stream
    # (log forgery); tab/newline are deliberately preserved.
    junk = "\nplain junk \x1b[31mred\x1b[0m and \x00 null"
    with caplog.at_level(logging.WARNING, logger=EXTRACT_LOGGER):
        parse_llm_response(SAMPLE_LLM_RESPONSE + junk)
    assert "\x1b" not in caplog.text
    assert "\x00" not in caplog.text
    assert "plain junk" in caplog.text


def test_salvaged_parse_without_confidence_skips_penalty(caplog):
    # Salvage + missing confidence: no penalty math, confidence stays None
    # (the pipeline routes None to needs_review).
    data = json.loads(SAMPLE_LLM_RESPONSE)
    del data["extraction_confidence"]
    with caplog.at_level(logging.WARNING, logger=EXTRACT_LOGGER):
        result = parse_llm_response("Here is the data:\n" + json.dumps(data))
    assert result.extraction_confidence is None
    assert result.parse_salvaged is True
    assert "confidence penalized" not in caplog.text


def test_parse_rejects_schema_less_object_with_trailing_payload():
    # A decoy object sharing no extraction keys must not beat the real payload.
    text = 'Example output: {"example": 1} and the actual data:\n' + SAMPLE_LLM_RESPONSE
    result = parse_llm_response(text)
    assert result.vendor_name == "Office Depot"
    assert result.total_amount == 354.51


def test_salvaged_parse_penalizes_confidence(caplog):
    # T3 salvage = the model deviated from instructions; trust it 10% less.
    with caplog.at_level(logging.WARNING, logger=EXTRACT_LOGGER):
        result = parse_llm_response("Here is the extracted data:\n" + SAMPLE_LLM_RESPONSE)
    assert result.extraction_confidence == pytest.approx(0.855)
    assert "confidence penalized" in caplog.text


def test_fenced_parse_penalizes_confidence():
    # T2 (fenced despite instructions) carries the same penalty as T3.
    result = parse_llm_response(f"```json\n{SAMPLE_LLM_RESPONSE}\n```")
    assert result.extraction_confidence == pytest.approx(0.855)


def test_direct_parse_keeps_full_confidence():
    result = parse_llm_response(SAMPLE_LLM_RESPONSE)
    assert result.extraction_confidence == 0.95


def test_parse_coerces_string_numerics():
    # json_object mode guarantees valid JSON, not types — a string "354.51"
    # must not crash the pipeline's confidence gate or pollute REAL columns.
    data = json.loads(SAMPLE_LLM_RESPONSE)
    data.update({"total_amount": "354.51", "tax_amount": "51.51", "subtotal": "N/A", "extraction_confidence": "0.95"})
    result = parse_llm_response(json.dumps(data))
    assert result.total_amount == 354.51
    assert result.tax_amount == 51.51
    assert result.subtotal is None  # unparseable numeric degrades to None, not a crash
    assert result.extraction_confidence == 0.95


def test_parse_failure_logs_head_and_tail(caplog):
    # The old text[:2000] truncation hid the "Extra data" at char 4846 (#215).
    long_garbage = "HEAD_MARKER " + ("x" * 5000) + " TAIL_MARKER"
    with caplog.at_level(logging.ERROR, logger=EXTRACT_LOGGER):
        with pytest.raises(ValueError):
            parse_llm_response(long_garbage)
    assert "HEAD_MARKER" in caplog.text
    assert "TAIL_MARKER" in caplog.text
    assert "chars omitted" in caplog.text

def test_document_type_override():
    response = json.loads(SAMPLE_LLM_RESPONSE)
    response["vendor_tax_id"] = "515000000"
    result = parse_llm_response(json.dumps(response))
    assert result.vendor_tax_id == "515000000"

@patch("backend.processing.extract.litellm_completion")
def test_extract_document_calls_llm(mock_completion):
    mock_completion.return_value = mock_llm_response()
    result = extract_document(page_images=[b"fake-png-bytes"], model="gemini/gemini-3-flash-preview", api_key="test-key", business_names=["Acme"], business_addresses=["123 Main"], business_tax_ids=["515000000"], expense_categories=[{"name": "office_supplies", "description": "Office stuff"}], issued_categories=[{"name": "Tax Invoice", "description": "Standard invoice"}])
    assert result.extraction.vendor_name == "Office Depot"
    assert result.tokens_in == 1000
    assert result.tokens_out == 500
    mock_completion.assert_called_once()


_EXTRACT_ARGS = dict(page_images=[b"fake-png-bytes"], model="gemini/gemini-3-flash-preview", api_key="test-key", business_names=["Acme"], business_addresses=["123 Main"], business_tax_ids=["515000000"], expense_categories=[{"name": "office_supplies", "description": "Office stuff"}], issued_categories=[])


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_requests_json_mode(mock_completion):
    mock_completion.return_value = mock_llm_response()
    extract_document(**_EXTRACT_ARGS)
    kwargs = mock_completion.call_args.kwargs
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["drop_params"] is True


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_json_mode_off(mock_completion):
    mock_completion.return_value = mock_llm_response()
    extract_document(**_EXTRACT_ARGS, json_mode=False)
    kwargs = mock_completion.call_args.kwargs
    assert "response_format" not in kwargs
    assert "drop_params" not in kwargs


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_default_temperature_is_one(mock_completion):
    # Issue #11: Gemini 3 is tuned for temperature 1.0 — the default must match.
    mock_completion.return_value = mock_llm_response()
    extract_document(**_EXTRACT_ARGS)
    assert mock_completion.call_args.kwargs["temperature"] == 1.0


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_truncated_response_raises(mock_completion):
    mock_completion.return_value = mock_llm_response(content='{"vendor_name": "Off', finish_reason="length")
    with pytest.raises(ValueError, match="truncated at max_tokens"):
        extract_document(**_EXTRACT_ARGS)


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_empty_response_raises(mock_completion):
    mock_completion.return_value = mock_llm_response(content=None)
    with pytest.raises(ValueError, match="empty response"):
        extract_document(**_EXTRACT_ARGS)


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_truncated_but_complete_object_salvaged(mock_completion):
    # The issue #10 shape taken to the extreme: complete object, then rambling
    # that hits the token cap. The ladder must salvage it, not hard-fail.
    mock_completion.return_value = mock_llm_response(content=SAMPLE_LLM_RESPONSE + "\n\nrambling that ran into the cap", finish_reason="length")
    result = extract_document(**_EXTRACT_ARGS)
    assert result.extraction.vendor_name == "Office Depot"


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_unparseable_non_truncated_reraises(mock_completion):
    # Parse failure on a NON-truncated response must propagate the parse
    # error unchanged, not get rewritten as a truncation error.
    mock_completion.return_value = mock_llm_response(content="this is not json", finish_reason="stop")
    with pytest.raises(ValueError, match="Failed to parse"):
        extract_document(**_EXTRACT_ARGS)


# --- Issue #12: retry LLM extraction on parse failure -----------------------
#
#   parse_retries=N  ── garbage ──► retry ──► valid ──► return (tokens summed)
#                    └─ garbage ──► ... ──► garbage ──► raise last ParseFailure
#   parse_retries=0  ── garbage ──► raise immediately (0 retries)
#   truncated/empty  ── raise plain ValueError, NEVER retried (call_count == 1)


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_retries_parse_failure_then_succeeds(mock_completion):
    # Case 1 (AC): a garbage response followed by a valid one processes the
    # document normally when a retry is allowed.
    mock_completion.side_effect = [mock_llm_response(content="this is not json"), mock_llm_response()]
    result = extract_document(**_EXTRACT_ARGS, parse_retries=1)
    assert result.extraction.vendor_name == "Office Depot"
    assert mock_completion.call_count == 2


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_retries_exhausted_raises_last_parse_error(mock_completion):
    # Case 2: retries exhausted -> the document fails with the parse error,
    # exactly as today. N=1 means one original + one retry = 2 calls.
    mock_completion.side_effect = [mock_llm_response(content="not json"), mock_llm_response(content="still not json")]
    with pytest.raises(ParseFailure, match="Failed to parse"):
        extract_document(**_EXTRACT_ARGS, parse_retries=1)
    assert mock_completion.call_count == 2


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_zero_retries_fails_immediately(mock_completion):
    # Case 3 (AC): N=0 preserves current behavior — fail on the first parse
    # failure with no retry.
    mock_completion.side_effect = [mock_llm_response(content="not json"), mock_llm_response()]
    with pytest.raises(ParseFailure, match="Failed to parse"):
        extract_document(**_EXTRACT_ARGS, parse_retries=0)
    assert mock_completion.call_count == 1


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_accumulates_tokens_across_attempts(mock_completion):
    # Case 4: tokens/cost must count ALL attempts (issue #12), including the
    # failed parse. Each mock response reports 1000 in / 500 out.
    mock_completion.side_effect = [mock_llm_response(content="not json"), mock_llm_response()]
    result = extract_document(**_EXTRACT_ARGS, parse_retries=1)
    assert result.tokens_in == 2000
    assert result.tokens_out == 1000


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_logs_warning_per_retry(mock_completion, caplog):
    # Case 5: each retry is logged at WARNING with the attempt count.
    mock_completion.side_effect = [mock_llm_response(content="not json"), mock_llm_response()]
    with caplog.at_level(logging.WARNING, logger=EXTRACT_LOGGER):
        extract_document(**_EXTRACT_ARGS, parse_retries=1)
    assert "parse failed (attempt 1/2), retrying" in caplog.text


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_truncated_response_not_retried(mock_completion):
    # Case 6a: a truncated response is a max_tokens problem, not transient —
    # it must fail fast without consuming a retry.
    mock_completion.side_effect = [mock_llm_response(content='{"vendor_name": "Off', finish_reason="length"), mock_llm_response()]
    with pytest.raises(ValueError, match="truncated at max_tokens"):
        extract_document(**_EXTRACT_ARGS, parse_retries=1)
    assert mock_completion.call_count == 1


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_empty_response_not_retried(mock_completion):
    # Case 6b: an empty (content-filter/safety-block) response must not retry —
    # re-calling a blocked prompt only burns tokens.
    mock_completion.side_effect = [mock_llm_response(content=None), mock_llm_response()]
    with pytest.raises(ValueError, match="empty response"):
        extract_document(**_EXTRACT_ARGS, parse_retries=1)
    assert mock_completion.call_count == 1


# --- Issue #12 (review hardening): parse_retries is coerced and clamped -------
# A negative value would make range() empty -> the loop falls off the end and
# returns None -> the pipeline crashes with a cryptic AttributeError. A non-int
# (reachable via the DB settings path, which skips _parse_value) would raise
# TypeError inside range(). A huge value would fire hundreds of paid LLM calls.


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_negative_retries_clamped_to_zero(mock_completion):
    # A negative config must degrade to "no retry", not silently return None.
    mock_completion.side_effect = [mock_llm_response(content="not json"), mock_llm_response()]
    with pytest.raises(ParseFailure, match="Failed to parse"):
        extract_document(**_EXTRACT_ARGS, parse_retries=-3)
    assert mock_completion.call_count == 1


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_string_retries_coerced(mock_completion):
    # The DB settings path can return a JSON string; extract_document must
    # coerce it rather than blow up in range().
    mock_completion.side_effect = [mock_llm_response(content="not json"), mock_llm_response()]
    result = extract_document(**_EXTRACT_ARGS, parse_retries="1")
    assert result.extraction.vendor_name == "Office Depot"
    assert mock_completion.call_count == 2


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_non_numeric_retries_treated_as_zero(mock_completion):
    # A non-numeric junk value must not crash — treat as 0 retries.
    mock_completion.side_effect = [mock_llm_response(content="not json"), mock_llm_response()]
    with pytest.raises(ParseFailure, match="Failed to parse"):
        extract_document(**_EXTRACT_ARGS, parse_retries="lots")
    assert mock_completion.call_count == 1


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_retries_capped(mock_completion):
    # A huge config is clamped so one poison document can't fire unbounded
    # sequential paid calls. Cap+1 attempts, then fail.
    mock_completion.side_effect = [mock_llm_response(content="not json")] * (_MAX_PARSE_RETRIES + 5)
    with pytest.raises(ParseFailure, match="Failed to parse"):
        extract_document(**_EXTRACT_ARGS, parse_retries=999)
    assert mock_completion.call_count == _MAX_PARSE_RETRIES + 1


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_retry_then_truncated_raises_truncation(mock_completion):
    # Retry interaction (review D2): a parse failure that RETRIES into a
    # truncated response must surface the truncation error, not loop again —
    # the retry consumed attempt 1, the truncation ends it at attempt 2.
    mock_completion.side_effect = [
        mock_llm_response(content="not json"),
        mock_llm_response(content='{"vendor_name": "Off', finish_reason="length"),
    ]
    with pytest.raises(ValueError, match="truncated at max_tokens"):
        extract_document(**_EXTRACT_ARGS, parse_retries=1)
    assert mock_completion.call_count == 2


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_salvaged_parse_on_retry_succeeds(mock_completion):
    # Retry interaction (review D2): a salvage-tier (T2/T3) parse that succeeds
    # on the retry attempt returns normally — salvage is only rejected when the
    # response is ALSO truncated, which this one is not.
    mock_completion.side_effect = [
        mock_llm_response(content="not json"),
        mock_llm_response(content="Sure! Here is the data:\n" + SAMPLE_LLM_RESPONSE),
    ]
    result = extract_document(**_EXTRACT_ARGS, parse_retries=1)
    assert result.extraction.vendor_name == "Office Depot"
    assert result.extraction.parse_salvaged is True
    assert mock_completion.call_count == 2


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_truncated_fragment_rejected(mock_completion):
    # Truncated response whose only parseable object is an inner fragment:
    # must raise the actionable truncation error, not file a junk record.
    fragment = SAMPLE_LLM_RESPONSE[:SAMPLE_LLM_RESPONSE.index("}") + 1]
    mock_completion.return_value = mock_llm_response(content=fragment, finish_reason="length")
    with pytest.raises(ValueError, match="truncated at max_tokens"):
        extract_document(**_EXTRACT_ARGS)


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_truncated_salvaged_parse_rejected(mock_completion):
    # Even a full-looking payload recovered via the SALVAGE tier is
    # untrustworthy when the response was truncated — T1 only.
    salvaged = "Here is the data:\n" + SAMPLE_LLM_RESPONSE
    mock_completion.return_value = mock_llm_response(content=salvaged, finish_reason="length")
    with pytest.raises(ValueError, match="truncated at max_tokens"):
        extract_document(**_EXTRACT_ARGS)


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_truncated_with_none_content(mock_completion):
    # finish_reason=length with content=None must raise the truncation error,
    # not a TypeError from slicing None.
    mock_completion.return_value = mock_llm_response(content=None, finish_reason="length")
    with pytest.raises(ValueError, match="truncated at max_tokens"):
        extract_document(**_EXTRACT_ARGS)


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_content_filter_reason_in_error(mock_completion):
    # Gemini safety blocks arrive as finish_reason=content_filter with empty
    # content — the stored error must carry the reason to be diagnosable.
    mock_completion.return_value = mock_llm_response(content=None, finish_reason="content_filter")
    with pytest.raises(ValueError, match="content_filter"):
        extract_document(**_EXTRACT_ARGS)


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_list_content_raises_cleanly(mock_completion):
    # Some providers return content as a list of parts; guard against a
    # cryptic AttributeError deep in the parser.
    mock_completion.return_value = mock_llm_response(content=[{"type": "text", "text": "{}"}])
    with pytest.raises(ValueError, match="unexpected content type"):
        extract_document(**_EXTRACT_ARGS)


# --- Reasoning effort (issue #13) ---

from backend.processing.extract import normalize_reasoning_effort, reasoning_effort_kwargs

_EXTRACT_ARGS_NO_MODEL = {k: v for k, v in _EXTRACT_ARGS.items() if k != "model"}


@pytest.mark.parametrize("raw,expected", [
    ("high", "high"), ("HIGH", "high"), (" low ", "low"), ("medium", "medium"),
    ("minimal", "minimal"), ("none", "none"),
    ("bogus", "none"), ("", "none"), (None, "none"), (5, "none"), (["high"], "none"),
])
def test_normalize_reasoning_effort(raw, expected):
    assert normalize_reasoning_effort(raw) == expected


def test_reasoning_effort_kwargs_supporting_model():
    assert reasoning_effort_kwargs("gemini/gemini-3-flash-preview", "high") == {"reasoning_effort": "high", "drop_params": True}


def test_reasoning_effort_kwargs_none_sends_nothing():
    assert reasoning_effort_kwargs("gemini/gemini-3-flash-preview", "none") == {}


def test_reasoning_effort_kwargs_nonreasoning_model_sends_nothing():
    assert reasoning_effort_kwargs("gpt-4o", "high") == {}


def test_reasoning_effort_kwargs_unknown_model_sends_nothing():
    assert reasoning_effort_kwargs("some/unknown-model-xyz", "high") == {}


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_no_reasoning_by_default(mock_completion):
    # Default (none) must be byte-identical to today: no reasoning_effort sent.
    mock_completion.return_value = mock_llm_response()
    extract_document(**_EXTRACT_ARGS)
    assert "reasoning_effort" not in mock_completion.call_args.kwargs


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_reasoning_effort_on_supporting_model(mock_completion):
    mock_completion.return_value = mock_llm_response()
    extract_document(**_EXTRACT_ARGS, reasoning_effort="high")
    kwargs = mock_completion.call_args.kwargs
    assert kwargs["reasoning_effort"] == "high"
    assert kwargs["drop_params"] is True


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_reasoning_effort_skipped_on_nonreasoning_model(mock_completion):
    # gpt-4o can't reason: the param must be withheld so litellm never raises
    # UnsupportedParamsError, even when the user set an effort level.
    mock_completion.return_value = mock_llm_response()
    extract_document(**_EXTRACT_ARGS_NO_MODEL, model="gpt-4o", reasoning_effort="high")
    assert "reasoning_effort" not in mock_completion.call_args.kwargs


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_invalid_reasoning_effort_ignored(mock_completion):
    mock_completion.return_value = mock_llm_response()
    extract_document(**_EXTRACT_ARGS, reasoning_effort="turbo")
    assert "reasoning_effort" not in mock_completion.call_args.kwargs


def test_build_prompt_separates_expense_and_issued_categories():
    prompt = build_extraction_prompt(
        business_names=["Acme"],
        business_addresses=["123 Main"],
        business_tax_ids=["515000000"],
        expense_categories=[
            {"name": "Office Supplies", "description": "Office stuff"},
            {"name": "Travel", "description": "Travel expenses"},
        ],
        issued_categories=[
            {"name": "Tax Invoice", "description": "Standard invoice with VAT"},
            {"name": "Credit Note", "description": "Cancels a previous invoice"},
        ],
    )
    assert "Expense Categories" in prompt
    assert "Issued Document Categories" in prompt
    assert "Office Supplies" in prompt
    assert "Tax Invoice" in prompt
    assert "Credit Note" in prompt
    # Verify they're in separate sections
    expense_pos = prompt.index("Expense Categories")
    issued_pos = prompt.index("Issued Document Categories")
    office_pos = prompt.index("Office Supplies")
    tax_inv_pos = prompt.index("Tax Invoice")
    assert expense_pos < office_pos < issued_pos < tax_inv_pos
