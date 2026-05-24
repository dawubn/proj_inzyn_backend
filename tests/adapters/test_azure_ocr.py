"""
Unit tests for AzureOCRAdapter.

Azure SDK is mocked, no real API calls are made.
Tests verify mapping logic and error handling.
"""

from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from app.adapters.azure_ocr import AzureOCRAdapter, OCRPage, OCRResult
from app.core.exceptions import OCRServiceError


def _make_mock_azure_result(
    content: str = "Sample text",
    pages: int = 2,
    word_confidence: float = 0.95,
) -> MagicMock:
    """Build a minimal mock object"""
    result = MagicMock()
    result.content = content

    mock_pages = []
    for i in range(pages):
        page = MagicMock()
        page.page_number = i + 1
        page.width = 210.0
        page.height = 297.0

        line = MagicMock()
        line.content = f"Line {i + 1}"
        page.lines = [line]

        word = MagicMock()
        word.confidence = word_confidence
        page.words = [word]

        mock_pages.append(page)

    result.pages = mock_pages
    result.documents = []
    result.as_dict.return_value = {"content": content, "pages": []}
    return result


@pytest.fixture
def adapter() -> AzureOCRAdapter:
    with patch("app.adapters.azure_ocr.DocumentIntelligenceClient"):
        return AzureOCRAdapter()


class TestOCRResultMapping:
    def test_maps_text_content(self, adapter: AzureOCRAdapter) -> None:
        azure_result = _make_mock_azure_result(content="Invoice #123")
        result = adapter._map_result(azure_result)
        assert result.text_content == "Invoice #123"

    def test_maps_page_count(self, adapter: AzureOCRAdapter) -> None:
        azure_result = _make_mock_azure_result(pages=3)
        result = adapter._map_result(azure_result)
        assert result.page_count == 3
        assert len(result.pages) == 3

    def test_maps_page_lines(self, adapter: AzureOCRAdapter) -> None:
        azure_result = _make_mock_azure_result(pages=1)
        result = adapter._map_result(azure_result)
        assert result.pages[0].lines == ["Line 1"]

    def test_maps_word_confidence(self, adapter: AzureOCRAdapter) -> None:
        azure_result = _make_mock_azure_result(word_confidence=0.88)
        result = adapter._map_result(azure_result)
        assert result.confidence == pytest.approx(0.88, abs=0.001)

    def test_uses_document_confidence_over_word_confidence(self, adapter: AzureOCRAdapter) -> None:
        azure_result = _make_mock_azure_result()
        doc = MagicMock()
        doc.confidence = 0.75
        azure_result.documents = [doc]

        result = adapter._map_result(azure_result)
        assert result.confidence == pytest.approx(0.75, abs=0.001)

    def test_reconstructs_text_from_pages_when_content_empty(
        self, adapter: AzureOCRAdapter
    ) -> None:
        azure_result = _make_mock_azure_result(content="", pages=2)
        result = adapter._map_result(azure_result)
        assert "Line 1" in result.text_content
        assert "Line 2" in result.text_content

    def test_confidence_none_when_no_words(self, adapter: AzureOCRAdapter) -> None:
        azure_result = _make_mock_azure_result()
        for page in azure_result.pages:
            page.words = []
        azure_result.documents = []
        result = adapter._map_result(azure_result)
        assert result.confidence is None

    def test_provider_is_azure(self, adapter: AzureOCRAdapter) -> None:
        result = adapter._map_result(_make_mock_azure_result())
        assert result.provider == "azure"

    def test_returns_ocr_result_instance(self, adapter: AzureOCRAdapter) -> None:
        result = adapter._map_result(_make_mock_azure_result())
        assert isinstance(result, OCRResult)

    def test_pages_are_ocr_page_instances(self, adapter: AzureOCRAdapter) -> None:
        result = adapter._map_result(_make_mock_azure_result(pages=2))
        assert all(isinstance(p, OCRPage) for p in result.pages)


class TestAnalyzeDocument:
    def test_calls_azure_and_returns_result(self, adapter: AzureOCRAdapter) -> None:
        mock_client = cast(MagicMock, adapter._client)
        mock_result = _make_mock_azure_result(content="Test content", pages=1)
        mock_client.begin_analyze_document.return_value.result.return_value = mock_result

        result = adapter.analyze_document(b"fake-pdf-bytes", "application/pdf")

        assert isinstance(result, OCRResult)
        assert result.text_content == "Test content"
        assert result.page_count == 1
        mock_client.begin_analyze_document.assert_called_once()

    def test_raises_ocr_service_error_on_azure_http_error(self, adapter: AzureOCRAdapter) -> None:
        from azure.core.exceptions import HttpResponseError

        mock_client = cast(MagicMock, adapter._client)
        mock_client.begin_analyze_document.side_effect = HttpResponseError(message="Unauthorized")

        with pytest.raises(OCRServiceError, match="Azure OCR error"):
            adapter.analyze_document(b"bytes", "application/pdf")

    def test_raises_ocr_service_error_on_network_error(self, adapter: AzureOCRAdapter) -> None:
        from azure.core.exceptions import ServiceRequestError

        mock_client = cast(MagicMock, adapter._client)
        mock_client.begin_analyze_document.side_effect = ServiceRequestError(
            message="Connection refused"
        )

        with pytest.raises(OCRServiceError, match="Azure OCR error"):
            adapter.analyze_document(b"bytes", "application/pdf")

    def test_raises_ocr_service_error_on_unexpected_exception(
        self, adapter: AzureOCRAdapter
    ) -> None:
        mock_client = cast(MagicMock, adapter._client)
        mock_client.begin_analyze_document.side_effect = RuntimeError("Boom")

        with pytest.raises(OCRServiceError, match="Unexpected OCR error"):
            adapter.analyze_document(b"bytes", "application/pdf")
