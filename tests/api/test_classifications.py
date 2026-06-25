import pytest
from httpx import AsyncClient
from main import app

from app.api.v1.classifications import _classification_service
from app.enums.document import DocumentType


class _FakeClassificationService:
    def classify(self, text: str) -> tuple[DocumentType, float, dict[str, float]]:
        if "patent" in text.lower():
            return DocumentType.PATENT, 0.91, {"patent": 0.91, "manual": 0.09}
        return DocumentType.MANUAL, 0.62, {"manual": 0.62, "patent": 0.38}


async def _get_auth_headers(
    client: AsyncClient, email: str = "classification_user@example.com"
) -> dict[str, str]:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "Str0ngPass!",
            "full_name": "Classification User",
            "role": "business_user",
        },
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "Str0ngPass!"},
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_classify_endpoint_accepts_azure_content_alias(client: AsyncClient) -> None:
    app.dependency_overrides[_classification_service] = lambda: _FakeClassificationService()
    try:
        headers = await _get_auth_headers(client)
        payload = {"content": "Patent claim invention embodiment"}

        response = await client.post("/api/v1/classify", headers=headers, json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["document_type"] == "patent"
        assert data["confidence"] == 0.91
        assert data["all_scores"]["patent"] == 0.91
    finally:
        app.dependency_overrides.pop(_classification_service, None)


@pytest.mark.asyncio
async def test_classify_endpoint_returns_422_on_missing_text(client: AsyncClient) -> None:
    app.dependency_overrides[_classification_service] = lambda: _FakeClassificationService()
    try:
        headers = await _get_auth_headers(client, email="classification_missing@example.com")
        response = await client.post(
            "/api/v1/classify",
            headers=headers,
            json={"ocr_raw_result": {}},
        )

        assert response.status_code == 422
    finally:
        app.dependency_overrides.pop(_classification_service, None)


