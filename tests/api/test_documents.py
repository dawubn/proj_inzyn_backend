import pytest
from httpx import AsyncClient


async def _get_auth_headers(client: AsyncClient, email: str = "doc_user@example.com") -> dict[str, str]:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Str0ngPass!", "full_name": "Doc User", "role": "business_user"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "Str0ngPass!"},
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


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
async def test_list_documents_empty(client: AsyncClient) -> None:
    headers = await _get_auth_headers(client, email="list_user@example.com")
    response = await client.get("/api/v1/documents", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data


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
