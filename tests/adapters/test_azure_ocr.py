"""
Unit tests for AzureOCRAdapter.

Azure SDK is mocked, no real API calls are made.
Tests verify mapping logic and error handling.
"""

import io
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

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
        request = mock_client.begin_analyze_document.call_args.args[1]
        assert request.as_dict() == {"base64Source": "ZmFrZS1wZGYtYnl0ZXM="}
        assert (
            mock_client.begin_analyze_document.call_args.kwargs["string_index_type"]
            == "unicodeCodePoint"
        )

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


def _tiff(pages: int) -> bytes:
    buffer = io.BytesIO()
    frames = [Image.new("RGB", (20, 20), "white") for _ in range(pages)]
    frames[0].save(buffer, format="TIFF", save_all=True, append_images=frames[1:])
    return buffer.getvalue()


def test_tiff_batches_preserve_every_page_and_rebase_unicode_spans(adapter):
    requests = []

    def analyze(data, content_type):
        with Image.open(io.BytesIO(data)) as image:
            requests.append(image.n_frames)
            pages = [
                {
                    "pageNumber": index + 1,
                    "width": 20,
                    "height": 20,
                    "lines": [{"content": "Żółć😀", "spans": [{"offset": 0, "length": 5}]}],
                    "words": [
                        {
                            "content": "Żółć😀",
                            "span": {"offset": 0, "length": 5},
                            "confidence": 0.99,
                        }
                    ],
                }
                for index in range(image.n_frames)
            ]
        assert content_type == "image/tiff"
        return adapter._map_result({"content": "Żółć😀", "pages": pages})

    with patch.object(adapter, "_analyze_bytes", side_effect=analyze):
        result = adapter.analyze_document(_tiff(3), "image/tiff")
    assert requests == [2, 1]
    assert result.page_count == 3
    assert [p.page_number for p in result.pages] == [1, 2, 3]
    assert result.raw["pages"][2]["lines"][0]["spans"][0]["offset"] == 6
    assert result.raw["pages"][2]["words"][0]["span"]["offset"] == 6
    assert result.text_content[6:11] == "Żółć😀"


def test_tiff_partial_result_is_an_error_instead_of_a_complete_document(adapter):
    partial = adapter._map_result({"content": "only page one", "pages": [{"pageNumber": 1}]})
    with (
        patch.object(adapter, "_analyze_bytes", return_value=partial),
        pytest.raises(OCRServiceError, match="wszystkich stron"),
    ):
        adapter.analyze_document(_tiff(2), "image/tiff")


def _make_pdf(pages: int) -> bytes:
    import fitz

    with fitz.open() as doc:
        for i in range(pages):
            doc.new_page().insert_text((72, 72), f"Page {i + 1}")
        return cast(bytes, doc.tobytes())


def _make_tiff(pages: int) -> bytes:
    import io

    from PIL import Image

    frames = [Image.new("RGB", (20, 20), "white") for _ in range(pages)]
    buf = io.BytesIO()
    frames[0].save(buf, format="TIFF", save_all=True, append_images=frames[1:])
    return buf.getvalue()


def _raw_chunk(texts: list[str]) -> dict[str, object]:
    """Raw Azure result (as_dict) for a chunk whose pages contain the given texts."""
    content = "\n".join(texts)
    pages = []
    offset = 0
    for number, text in enumerate(texts, start=1):
        pages.append(
            {
                "pageNumber": number,
                "spans": [{"offset": offset, "length": len(text)}],
                "lines": [{"content": text, "spans": [{"offset": offset, "length": len(text)}]}],
                "words": [{"content": text, "confidence": 0.9}],
            }
        )
        offset += len(text) + 1
    paragraphs = [{"content": t, "boundingRegions": [{"pageNumber": 1}]} for t in texts[:1]]
    return {"content": content, "pages": pages, "paragraphs": paragraphs}


class TestSplitIntoPageChunks:
    def test_splits_pdf_into_chunks(self) -> None:
        from app.adapters.azure_ocr import split_into_page_chunks

        chunks, page_count = split_into_page_chunks(_make_pdf(5), "application/pdf", 2)

        import fitz

        assert page_count == 5
        assert [len(fitz.open(stream=c, filetype="pdf")) for c in chunks] == [2, 2, 1]

    def test_splits_tiff_into_chunks(self) -> None:
        import io

        from PIL import Image

        from app.adapters.azure_ocr import split_into_page_chunks

        chunks, page_count = split_into_page_chunks(_make_tiff(3), "image/tiff", 2)

        assert page_count == 3
        assert [Image.open(io.BytesIO(c)).n_frames for c in chunks] == [2, 1]

    def test_short_document_is_not_split(self) -> None:
        from app.adapters.azure_ocr import split_into_page_chunks

        pdf = _make_pdf(2)
        assert split_into_page_chunks(pdf, "application/pdf", 2) == ([pdf], 2)

    def test_single_image_and_disabled_chunking(self) -> None:
        from app.adapters.azure_ocr import split_into_page_chunks

        assert split_into_page_chunks(b"png", "image/png", 2) == ([b"png"], None)
        assert split_into_page_chunks(b"pdf", "application/pdf", 0) == ([b"pdf"], None)


class TestAnalyzePaged:
    def test_merges_chunks_with_document_page_numbers_and_offsets(
        self, adapter: AzureOCRAdapter
    ) -> None:
        chunk_results = [
            OCRResult(
                raw=_raw_chunk(["one", "two"]),
                text_content="",
                pages=[],
                page_count=2,
                confidence=None,
            ),
            OCRResult(
                raw=_raw_chunk(["three"]), text_content="", pages=[], page_count=1, confidence=None
            ),
        ]
        with patch.object(adapter, "_analyze_bytes", side_effect=chunk_results) as analyze:
            result = adapter.analyze_paged(_make_pdf(3), "application/pdf")

        assert analyze.call_count == 2
        assert result.text_content == "one\ntwo\nthree"
        assert result.page_count == 3
        assert [p.lines for p in result.pages] == [["one"], ["two"], ["three"]]
        assert [p["pageNumber"] for p in result.raw["pages"]] == [1, 2, 3]
        third = result.raw["pages"][2]["lines"][0]["spans"][0]
        assert result.text_content[third["offset"] : third["offset"] + third["length"]] == "three"
        assert result.raw["paragraphs"][1]["boundingRegions"][0]["pageNumber"] == 3

    def test_single_chunk_calls_azure_once(self, adapter: AzureOCRAdapter) -> None:
        mock_client = cast(MagicMock, adapter._client)
        mock_client.begin_analyze_document.return_value.result.return_value = (
            _make_mock_azure_result(content="x", pages=1)
        )

        result = adapter.analyze_paged(_make_pdf(1), "application/pdf")

        assert result.text_content == "x"
        mock_client.begin_analyze_document.assert_called_once()


def test_paged_pdf_partial_result_is_an_error(adapter: AzureOCRAdapter) -> None:
    partial = adapter._map_result(_raw_chunk(["only page one"]))
    with (
        patch.object(adapter, "_analyze_bytes", return_value=partial),
        pytest.raises(OCRServiceError, match="wszystkich stron"),
    ):
        adapter.analyze_paged(_make_pdf(2), "application/pdf")
