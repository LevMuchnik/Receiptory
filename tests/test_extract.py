import json
import pytest
from unittest.mock import patch, MagicMock
from backend.processing.extract import (build_extraction_prompt, parse_llm_response, extract_document, ExtractionResult)

SAMPLE_LLM_RESPONSE = json.dumps({
    "receipt_date": "2026-01-15", "document_title": "Tax Invoice", "vendor_name": "Office Depot",
    "vendor_tax_id": "515234567", "vendor_receipt_id": "INV-2026-001", "client_name": None, "client_tax_id": None,
    "description": "Office supplies purchase",
    "line_items": [{"description": "Paper A4", "quantity": 5, "unit_price": 25.0}, {"description": "Ink cartridge", "quantity": 2, "unit_price": 89.0}],
    "subtotal": 303.0, "tax_amount": 51.51, "total_amount": 354.51, "currency": "ILS",
    "payment_method": "credit_card", "payment_identifier": "4580", "language": "he",
    "additional_fields": [], "raw_extracted_text": "Office Depot\nTax Invoice\n...",
    "document_type": "expense_receipt", "category": "office_supplies", "extraction_confidence": 0.95,
})

def test_build_prompt_includes_business_info():
    prompt = build_extraction_prompt(business_names=["Acme Corp", 'אקמה בע"מ'], business_addresses=["123 Main St", "רחוב ראשי 123"], business_tax_ids=["515000000"], categories=[{"name": "office_supplies", "description": "Office equipment and supplies"}, {"name": "travel", "description": "Travel expenses"}])
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

def test_document_type_override():
    response = json.loads(SAMPLE_LLM_RESPONSE)
    response["vendor_tax_id"] = "515000000"
    result = parse_llm_response(json.dumps(response))
    assert result.vendor_tax_id == "515000000"

@patch("backend.processing.extract.litellm_completion")
def test_extract_document_calls_llm(mock_completion):
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = SAMPLE_LLM_RESPONSE
    mock_response.usage.prompt_tokens = 1000
    mock_response.usage.completion_tokens = 500
    mock_completion.return_value = mock_response
    result = extract_document(page_images=[b"fake-png-bytes"], model="gemini/gemini-3-flash-preview", api_key="test-key", business_names=["Acme"], business_addresses=["123 Main"], business_tax_ids=["515000000"], categories=[{"name": "office_supplies", "description": "Office stuff"}])
    assert result.extraction.vendor_name == "Office Depot"
    assert result.tokens_in == 1000
    assert result.tokens_out == 500
    mock_completion.assert_called_once()
