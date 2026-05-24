from pathlib import Path

import pytest
from httpx import AsyncClient
from main import app

from app.api.v1 import redactions
from app.enums.redaction import SensitiveDataType
from app.services.redaction import PageRedactionReport, RedactionResult
from tests.api.test_documents import _get_auth_headers


class FakeRedactionService:
    def __init__(self, output_path: Path, temp_dir: Path) -> None:
        self.output_path = output_path
        self.temp_dir = temp_dir

    def redact_file(self, filename: str, content: bytes, content_type: str) -> RedactionResult:
        return RedactionResult(
            output_path=self.output_path,
            output_filename=f"{Path(filename).stem}_redacted.png",
            media_type=content_type,
            temp_dir=self.temp_dir,
            reports=[
                PageRedactionReport(
                    page_number=1,
                    word_count=4,
                    findings_count=1,
                    redacted_boxes_count=3,
                    detected_types={SensitiveDataType.EMAIL, SensitiveDataType.PERSON},
                )
            ],
        )

    def cleanup(self, temp_dir: Path) -> None:
        return None


@pytest.mark.asyncio
async def test_redaction_requires_auth(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/redactions",
        files={"file": ("test.png", b"content", "image/png")},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_redaction_rejects_invalid_extension(client: AsyncClient) -> None:
    headers = await _get_auth_headers(client, email="redaction_invalid@example.com")

    response = await client.post(
        "/api/v1/redactions",
        headers=headers,
        files={"file": ("test.txt", b"content", "text/plain")},
    )

    assert response.status_code == 415


@pytest.mark.asyncio
async def test_redaction_returns_file_with_report_headers(
    client: AsyncClient, tmp_path: Path
) -> None:
    headers = await _get_auth_headers(client, email="redaction_success@example.com")
    output_path = tmp_path / "redacted.png"
    output_path.write_bytes(b"redacted-content")
    fake_service = FakeRedactionService(output_path, tmp_path)
    app.dependency_overrides[redactions._redaction_service] = lambda: fake_service

    response = await client.post(
        "/api/v1/redactions",
        headers=headers,
        files={"file": ("test.png", b"content", "image/png")},
    )

    assert response.status_code == 200
    assert response.content == b"redacted-content"
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-redaction-findings-count"] == "1"
    assert response.headers["x-redaction-boxes-count"] == "3"
    assert response.headers["x-redaction-pages"] == "1"
    assert response.headers["x-redaction-types"] == "EMAIL,PERSON"
