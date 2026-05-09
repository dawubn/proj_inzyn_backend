import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.enums.document import DocumentStatus, FileExtension
from app.models.document import Document
from app.schemas.document import DocumentCreate
from app.services.document import DocumentService


@pytest.fixture
def mock_repo() -> MagicMock:
    repo = MagicMock()
    repo.create = AsyncMock()
    return repo


@pytest.fixture
def document_service(mock_repo: MagicMock) -> DocumentService:
    return DocumentService(mock_repo)


@pytest.mark.asyncio
async def test_upload_creates_document(
    document_service: DocumentService,
    mock_repo: MagicMock,
    tmp_path,
    monkeypatch,
) -> None:
    from app.core import config as cfg_module

    monkeypatch.setattr(cfg_module.settings, "STORAGE_PATH", str(tmp_path))
    monkeypatch.setattr(cfg_module.settings, "MAX_UPLOAD_SIZE_MB", 10)
    monkeypatch.setattr(cfg_module.settings, "ALLOWED_EXTENSIONS", ["pdf"])

    created_doc = Document(
        id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        original_filename="test.pdf",
        storage_path=str(tmp_path / "test.pdf"),
        file_extension=FileExtension.PDF,
        file_size_bytes=10,
        mime_type="application/pdf",
        status=DocumentStatus.UPLOADED,
    )
    mock_repo.create.return_value = created_doc

    owner_id = uuid.uuid4()
    result = await document_service.upload(
        owner_id=owner_id,
        filename="test.pdf",
        content=b"PDF content",
        content_type="application/pdf",
        metadata=DocumentCreate(),
    )
    assert result.status == DocumentStatus.UPLOADED
    mock_repo.create.assert_called_once()


@pytest.mark.asyncio
async def test_upload_rejects_large_file(document_service: DocumentService, monkeypatch) -> None:
    from app.core import config as cfg_module
    from app.core.exceptions import FileTooLargeError

    monkeypatch.setattr(cfg_module.settings, "MAX_UPLOAD_SIZE_MB", 1)
    monkeypatch.setattr(cfg_module.settings, "ALLOWED_EXTENSIONS", ["pdf"])

    with pytest.raises(FileTooLargeError):
        await document_service.upload(
            owner_id=uuid.uuid4(),
            filename="big.pdf",
            content=b"x" * (2 * 1024 * 1024),
            content_type="application/pdf",
            metadata=DocumentCreate(),
        )
