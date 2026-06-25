import pytest
from httpx import AsyncClient


async def _get_auth_headers(
    client: AsyncClient, email: str = "doc_user@example.com"
) -> dict[str, str]:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "Str0ngPass!",
            "full_name": "Doc User",
            "role": "business_user",
        },
    )
    await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "Str0ngPass!"},
    )
    # Cookies are automatically stored in client and sent with requests
    # Return empty dict, cookies will be sent automatically
    return {}


@pytest.mark.asyncio
async def test_upload_document(client: AsyncClient, tmp_path) -> None:
    headers = await _get_auth_headers(client)
    file_content = b"%PDF-1.4 test content"
    response = await client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("test.pdf", file_content, "application/pdf")},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["original_filename"] == "test.pdf"
    assert data["status"] == "uploaded"


@pytest.mark.asyncio
async def test_upload_invalid_extension(client: AsyncClient) -> None:
    headers = await _get_auth_headers(client, email="ext_user@example.com")
    response = await client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("virus.exe", b"MZ content", "application/octet-stream")},
    )
    assert response.status_code == 415


@pytest.mark.asyncio
async def test_get_document_not_found(client: AsyncClient) -> None:
    headers = await _get_auth_headers(client, email="notfound_user@example.com")
    fake_id = "00000000-0000-0000-0000-000000000000"
    response = await client.get(f"/api/v1/documents/{fake_id}", headers=headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_patch_document_type(client: AsyncClient, tmp_path) -> None:
    headers = await _get_auth_headers(client, email="patch_user@example.com")
    upload = await client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("doc.pdf", b"%PDF-1.4 test", "application/pdf")},
    )
    assert upload.status_code == 201
    doc_id = upload.json()["id"]

    response = await client.patch(
        f"/api/v1/documents/{doc_id}",
        headers=headers,
        json={"document_type": "contract"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["document_type"] == "contract"
    assert data["suggested_document_type"] is None


@pytest.mark.asyncio
async def test_patch_document_type_invalid_value(client: AsyncClient, tmp_path) -> None:
    headers = await _get_auth_headers(client, email="patch_invalid_user@example.com")
    upload = await client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("doc.pdf", b"%PDF-1.4 test", "application/pdf")},
    )
    doc_id = upload.json()["id"]

    response = await client.patch(
        f"/api/v1/documents/{doc_id}",
        headers=headers,
        json={"document_type": "not_a_real_type"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_patch_document_type_not_found(client: AsyncClient) -> None:
    headers = await _get_auth_headers(client, email="patch_notfound_user@example.com")
    fake_id = "00000000-0000-0000-0000-000000000000"
    response = await client.patch(
        f"/api/v1/documents/{fake_id}",
        headers=headers,
        json={"document_type": "invoice"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_patch_document_type_forbidden(client: AsyncClient, tmp_path) -> None:
    # Login as owner first, upload while owner cookie is active
    await _get_auth_headers(client, email="patch_owner@example.com")
    upload = await client.post(
        "/api/v1/documents",
        files={"file": ("doc.pdf", b"%PDF-1.4 test", "application/pdf")},
    )
    assert upload.status_code == 201
    doc_id = upload.json()["id"]

    # Now login as a different user — cookie switches to other user
    await _get_auth_headers(client, email="patch_other@example.com")
    response = await client.patch(
        f"/api/v1/documents/{doc_id}",
        json={"document_type": "invoice"},
    )
    assert response.status_code == 403
