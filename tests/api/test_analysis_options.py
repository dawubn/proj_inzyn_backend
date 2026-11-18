import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai_models import AI_MODEL_PROFILES, TEST_ISSUES_TOTAL
from app.core.config import settings
from app.enums.analysis import RedactionCategory
from app.models.document_analysis import DocumentAnalysis

OPTIONS_URL = "/api/v1/redactions/options"
TRIGGER_URL = "/api/v1/redactions/legal-analysis"


@pytest.fixture
def ai_models(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setattr(settings, "AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setattr(settings, "AZURE_OPENAI_KEY", "key")
    monkeypatch.setattr(settings, "AZURE_OPENAI_DEFAULT_MODEL", "gpt-4.1")
    monkeypatch.setattr(settings, "AZURE_OPENAI_MODELS", ["gpt-4.1", "gpt-4o-mini"])
    return ["gpt-4.1", "gpt-4o-mini"]


async def _login(client: AsyncClient, email: str) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Str0ngPass!", "name": "Options", "surname": "User"},
    )
    await client.post("/api/v1/auth/login", json={"email": email, "password": "Str0ngPass!"})


async def _upload(client: AsyncClient) -> str:
    upload = await client.post(
        "/api/v1/documents",
        files={"file": ("umowa.pdf", b"%PDF-1.4 test", "application/pdf")},
    )
    assert upload.status_code == 201
    document_id: str = upload.json()["id"]
    return document_id


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_models")
async def test_options_endpoint_describes_the_settings(client: AsyncClient) -> None:
    await _login(client, "options_list@example.com")

    response = await client.get(OPTIONS_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["redaction_categories"] == [category.value for category in RedactionCategory]
    assert body["ai_analysis_available"] is True
    assert body["default_ai_model"] == "gpt-4.1"
    assert [model["name"] for model in body["ai_models"]] == ["gpt-4.1", "gpt-4o-mini"]

    described, unknown = body["ai_models"]
    profile = AI_MODEL_PROFILES["gpt-4.1"]
    assert described["description"] == profile.description
    assert described["cost"] == profile.cost
    assert described["speed"] == profile.speed
    assert described["quality"] == profile.quality
    assert described["test_result"]["cost_usd"] == profile.test_cost_usd
    assert described["test_result"]["issues_total"] == TEST_ISSUES_TOTAL
    # A deployment without a profile is still offered, just without the annotation
    assert unknown == {
        "name": "gpt-4o-mini",
        "recommended": False,
        "description": None,
        "cost": None,
        "speed": None,
        "quality": None,
        "test_result": None,
    }


@pytest.mark.asyncio
async def test_options_endpoint_reports_missing_ai_configuration(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "AZURE_OPENAI_KEY", None)
    await _login(client, "options_no_ai@example.com")

    response = await client.get(OPTIONS_URL)

    assert response.status_code == 200
    assert response.json()["ai_analysis_available"] is False


@pytest.mark.asyncio
async def test_options_endpoint_requires_login(client: AsyncClient) -> None:
    client.cookies.clear()

    assert (await client.get(OPTIONS_URL)).status_code == 401


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_models")
async def test_analysis_started_without_options_uses_defaults(client: AsyncClient) -> None:
    await _login(client, "options_default@example.com")
    document_id = await _upload(client)

    trigger = await client.post(TRIGGER_URL, params={"document_id": document_id})
    assert trigger.status_code == 202

    details = await client.get(f"/api/v1/redactions/{trigger.json()['analysis_id']}")
    assert details.status_code == 200
    assert details.json()["options"] == {
        "anonymization_enabled": True,
        "redaction_categories": [category.value for category in RedactionCategory],
        "ai_analysis_enabled": True,
        # The default model is stored, so a later change of the default does not rewrite history
        "ai_model": "gpt-4.1",
    }
    assert "pipeline_params" not in details.json()


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_models")
async def test_analysis_is_started_with_chosen_options(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _login(client, "options_custom@example.com")
    document_id = await _upload(client)

    trigger = await client.post(
        TRIGGER_URL,
        params={
            "document_id": document_id,
            "redaction_categories": ["pesel", "person"],
            "ai_model": "gpt-4o-mini",
        },
    )
    assert trigger.status_code == 202
    analysis_id = trigger.json()["analysis_id"]

    expected = {
        "anonymization_enabled": True,
        "redaction_categories": ["person", "pesel"],
        "ai_analysis_enabled": True,
        "ai_model": "gpt-4o-mini",
    }
    analysis = await db_session.get(DocumentAnalysis, uuid.UUID(analysis_id))
    assert analysis is not None
    assert analysis.pipeline_params["options"] == expected
    details = await client.get(f"/api/v1/redactions/{analysis_id}")
    assert details.json()["options"] == expected
    listing = await client.get("/api/v1/redactions")
    assert listing.json()[0]["options"] == expected


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_models")
async def test_analysis_can_be_started_without_anonymization_and_ai(client: AsyncClient) -> None:
    await _login(client, "options_off@example.com")
    document_id = await _upload(client)

    trigger = await client.post(
        TRIGGER_URL,
        params={"document_id": document_id, "anonymize": "false", "ai_analysis": "false"},
    )
    assert trigger.status_code == 202

    details = await client.get(f"/api/v1/redactions/{trigger.json()['analysis_id']}")
    assert details.json()["options"] == {
        "anonymization_enabled": False,
        "redaction_categories": [],
        "ai_analysis_enabled": False,
        "ai_model": None,
    }


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_models")
@pytest.mark.parametrize(
    "params",
    [
        {"ai_model": "not-a-configured-model"},
        {"redaction_categories": ["fingerprints"]},
    ],
)
async def test_invalid_options_are_rejected(client: AsyncClient, params: dict[str, str]) -> None:
    await _login(client, f"options_invalid_{len(params)}{next(iter(params))}@example.com")
    document_id = await _upload(client)

    trigger = await client.post(TRIGGER_URL, params={"document_id": document_id, **params})

    assert trigger.status_code == 422
    listing = await client.get("/api/v1/redactions")
    assert listing.json() == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_models")
async def test_unknown_model_is_ignored_when_ai_analysis_is_off(client: AsyncClient) -> None:
    await _login(client, "options_model_off@example.com")
    document_id = await _upload(client)

    trigger = await client.post(
        TRIGGER_URL,
        params={"document_id": document_id, "ai_analysis": "false", "ai_model": "anything"},
    )

    assert trigger.status_code == 202
