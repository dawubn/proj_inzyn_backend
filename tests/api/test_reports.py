import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums.analysis import AnalysisStatus
from app.models.document import Document
from app.models.document_analysis import DocumentAnalysis


async def _login(client: AsyncClient, email: str) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "Str0ngPass!",
            "full_name": "Report User",
            "role": "business_user",
        },
    )
    await client.post("/api/v1/auth/login", json={"email": email, "password": "Str0ngPass!"})


async def _create_analysis(
    client: AsyncClient, db_session: AsyncSession, status: AnalysisStatus
) -> uuid.UUID:
    upload = await client.post(
        "/api/v1/documents",
        files={"file": ("umowa najmu.pdf", b"%PDF-1.4 test", "application/pdf")},
    )
    assert upload.status_code == 201
    document = await db_session.get(Document, uuid.UUID(upload.json()["id"]))
    assert document is not None
    analysis = DocumentAnalysis(
        document_id=document.id,
        owner_id=document.owner_id,
        status=status,
        legal_analysis_result={
            "summary": "Umowa najmu lokalu mieszkalnego.",
            "errors": [{"issue": "Brak daty zawarcia umowy", "severity": "high", "page": 1}],
            "applicable_laws": [],
        },
    )
    db_session.add(analysis)
    await db_session.flush()
    return analysis.id


@pytest.mark.asyncio
async def test_download_report_pdf(client: AsyncClient, db_session: AsyncSession) -> None:
    await _login(client, "report_pdf_user@example.com")
    analysis_id = await _create_analysis(client, db_session, AnalysisStatus.COMPLETED)

    response = await client.get(f"/api/v1/reports/{analysis_id}/pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="raport_umowa_najmu.pdf"')
    assert disposition.endswith("filename*=UTF-8''raport_umowa%20najmu.pdf")
    assert response.content.startswith(b"%PDF")


@pytest.mark.asyncio
async def test_download_report_pdf_requires_completed_analysis(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _login(client, "report_pending_user@example.com")
    analysis_id = await _create_analysis(client, db_session, AnalysisStatus.IN_PROGRESS)

    response = await client.get(f"/api/v1/reports/{analysis_id}/pdf")

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_download_report_pdf_of_other_user(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _login(client, "report_owner@example.com")
    analysis_id = await _create_analysis(client, db_session, AnalysisStatus.COMPLETED)
    await _login(client, "report_stranger@example.com")

    response = await client.get(f"/api/v1/reports/{analysis_id}/pdf")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_download_report_pdf_requires_auth(client: AsyncClient) -> None:
    response = await client.get(f"/api/v1/reports/{uuid.uuid4()}/pdf")

    assert response.status_code == 401
