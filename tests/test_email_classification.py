import pytest
from unittest.mock import patch, MagicMock
from backend.ingestion.url_triage import classify_email_documents, ClassificationDocument, triage_email_urls


@pytest.fixture
def mock_llm_settings(monkeypatch):
    """Provide LLM settings for triage tests."""
    monkeypatch.setenv("RECEIPTORY_LLM_MODEL", "test-model")
    monkeypatch.setenv("RECEIPTORY_LLM_API_KEY", "test-key")


def _make_doc(identifier: str, source: str = "attachment", image: bytes = b"fake-png") -> ClassificationDocument:
    return ClassificationDocument(identifier=identifier, source=source, first_page_image=image)


@pytest.mark.asyncio
async def test_classify_returns_matching_identifiers(mock_llm_settings):
    """LLM picks invoice.pdf from a list of attachments."""
    docs = [_make_doc("invoice.pdf"), _make_doc("logo.png")]
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '["invoice.pdf"]'

    with patch("backend.ingestion.url_triage.litellm_completion", return_value=mock_response):
        result = await classify_email_documents(
            sender_email="billing@vendor.com",
            subject="Your Invoice #123",
            body_text="Please find your invoice attached.",
            documents=docs,
        )
    assert result == ["invoice.pdf"]


@pytest.mark.asyncio
async def test_classify_returns_empty_when_none_qualify(mock_llm_settings):
    """LLM returns empty list when no document is financial."""
    docs = [_make_doc("newsletter.pdf")]
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '[]'

    with patch("backend.ingestion.url_triage.litellm_completion", return_value=mock_response):
        result = await classify_email_documents(
            sender_email="news@company.com",
            subject="Weekly Newsletter",
            body_text="Here is our newsletter.",
            documents=docs,
        )
    assert result == []


@pytest.mark.asyncio
async def test_classify_filters_invalid_identifiers(mock_llm_settings):
    """LLM returns an identifier not in the input — it gets filtered out."""
    docs = [_make_doc("real.pdf")]
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '["real.pdf", "hallucinated.pdf"]'

    with patch("backend.ingestion.url_triage.litellm_completion", return_value=mock_response):
        result = await classify_email_documents(
            sender_email="a@b.com", subject="test", body_text="", documents=docs,
        )
    assert result == ["real.pdf"]


@pytest.mark.asyncio
async def test_classify_fallback_on_llm_failure(mock_llm_settings):
    """On LLM error, fallback returns all identifiers."""
    docs = [_make_doc("a.pdf"), _make_doc("b.png")]
    with patch("backend.ingestion.url_triage.litellm_completion", side_effect=Exception("API down")):
        result = await classify_email_documents(
            sender_email="a@b.com", subject="test", body_text="", documents=docs,
        )
    assert set(result) == {"a.pdf", "b.png"}


@pytest.mark.asyncio
async def test_classify_fallback_when_llm_not_configured():
    """No LLM configured — fallback returns all identifiers."""
    docs = [_make_doc("a.pdf")]
    result = await classify_email_documents(
        sender_email="a@b.com", subject="test", body_text="", documents=docs,
    )
    assert result == ["a.pdf"]


@pytest.mark.asyncio
async def test_classify_empty_documents():
    """No documents provided — returns empty list."""
    result = await classify_email_documents(
        sender_email="a@b.com", subject="test", body_text="", documents=[],
    )
    assert result == []


@pytest.mark.asyncio
async def test_triage_urls_returns_candidates(mock_llm_settings):
    """LLM picks invoice URL from a list."""
    urls = ["https://billing.com/invoice/123", "https://company.com/unsubscribe"]
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '["https://billing.com/invoice/123"]'

    with patch("backend.ingestion.url_triage.litellm_completion", return_value=mock_response):
        result = await triage_email_urls(
            sender_email="billing@vendor.com",
            subject="Your Invoice",
            body_text="Click here to view your invoice.",
            urls=urls,
        )
    assert result == ["https://billing.com/invoice/123"]


@pytest.mark.asyncio
async def test_triage_urls_fallback_on_failure(mock_llm_settings):
    """On LLM failure, returns all URLs."""
    urls = ["https://a.com", "https://b.com"]
    with patch("backend.ingestion.url_triage.litellm_completion", side_effect=Exception("fail")):
        result = await triage_email_urls(
            sender_email="a@b.com", subject="test", body_text="", urls=urls,
        )
    assert set(result) == {"https://a.com", "https://b.com"}


@pytest.mark.asyncio
async def test_triage_urls_empty():
    """No URLs — returns empty list."""
    result = await triage_email_urls(
        sender_email="a@b.com", subject="test", body_text="", urls=[],
    )
    assert result == []
