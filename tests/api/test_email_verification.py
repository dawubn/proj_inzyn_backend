from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.email_verification_token import EmailVerificationToken
from app.models.user import User

PASSWORD = "Str0ngPass!"
REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
VERIFY_URL = "/api/v1/auth/verify-email"
RESEND_URL = "/api/v1/auth/resend-verification"


@pytest.fixture
def sent_emails(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Collects (recipient, verification link) instead of sending real emails."""
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.api.v1.auth.send_verification_email",
        lambda to, url: sent.append((to, url)),
    )
    return sent


@pytest.fixture
def verification_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "EMAIL_VERIFICATION_REQUIRED", True)


@pytest.fixture
def no_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS", 0)


async def _register(client: AsyncClient, email: str) -> dict[str, object]:
    response = await client.post(
        REGISTER_URL,
        json={"email": email, "password": PASSWORD, "name": "Verify", "surname": "User"},
    )
    assert response.status_code == 201
    body: dict[str, object] = response.json()
    return body


async def _login(client: AsyncClient, email: str) -> int:
    response = await client.post(LOGIN_URL, json={"email": email, "password": PASSWORD})
    status: int = response.status_code
    return status


def _token_from(url: str) -> str:
    return parse_qs(urlparse(url).query)["token"][0]


@pytest.mark.asyncio
async def test_without_required_verification_account_works_at_once(
    client: AsyncClient, sent_emails: list[tuple[str, str]]
) -> None:
    body = await _register(client, "verify_off@example.com")

    assert body["is_verified"] is True
    assert sent_emails == []
    assert await _login(client, "verify_off@example.com") == 200


@pytest.mark.asyncio
@pytest.mark.usefixtures("verification_required")
async def test_new_account_gets_link_and_cannot_log_in_before_opening_it(
    client: AsyncClient, sent_emails: list[tuple[str, str]]
) -> None:
    email = "verify_new@example.com"
    body = await _register(client, email)

    assert body["is_verified"] is False
    assert len(sent_emails) == 1
    to, url = sent_emails[0]
    assert to == email
    assert url.startswith(f"{settings.FRONTEND_URL.rstrip('/')}/verify-email?token=")

    response = await client.post(LOGIN_URL, json={"email": email, "password": PASSWORD})
    assert response.status_code == 403
    assert response.json()["code"] == "email_not_verified"


@pytest.mark.asyncio
@pytest.mark.usefixtures("verification_required")
async def test_wrong_password_does_not_reveal_unverified_account(
    client: AsyncClient, sent_emails: list[tuple[str, str]]
) -> None:
    await _register(client, "verify_wrong_pass@example.com")

    response = await client.post(
        LOGIN_URL, json={"email": "verify_wrong_pass@example.com", "password": "Wr0ngPass!"}
    )

    assert response.status_code == 401
    assert "code" not in response.json()


@pytest.mark.asyncio
@pytest.mark.usefixtures("verification_required")
async def test_link_verifies_account_once(
    client: AsyncClient, sent_emails: list[tuple[str, str]]
) -> None:
    email = "verify_link@example.com"
    await _register(client, email)
    token = _token_from(sent_emails[-1][1])

    assert (await client.post(VERIFY_URL, json={"token": token})).status_code == 204
    assert await _login(client, email) == 200

    again = await client.post(VERIFY_URL, json={"token": token})
    assert again.status_code == 400


@pytest.mark.asyncio
async def test_unknown_token_is_rejected(client: AsyncClient) -> None:
    response = await client.post(VERIFY_URL, json={"token": "not-a-real-token"})

    assert response.status_code == 400
    assert response.json()["detail"] == "Verification link is invalid or has expired"


@pytest.mark.asyncio
@pytest.mark.usefixtures("verification_required")
async def test_expired_link_is_rejected(
    client: AsyncClient, db_session: AsyncSession, sent_emails: list[tuple[str, str]]
) -> None:
    email = "verify_expired@example.com"
    body = await _register(client, email)
    token = _token_from(sent_emails[-1][1])
    await db_session.execute(
        update(EmailVerificationToken)
        .where(EmailVerificationToken.user_id == body["id"])
        .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    )
    await db_session.commit()

    assert (await client.post(VERIFY_URL, json={"token": token})).status_code == 400
    assert await _login(client, email) == 403


@pytest.mark.asyncio
@pytest.mark.usefixtures("verification_required", "no_cooldown")
async def test_new_link_replaces_the_old_one(
    client: AsyncClient, sent_emails: list[tuple[str, str]]
) -> None:
    email = "verify_resend@example.com"
    await _register(client, email)
    old_token = _token_from(sent_emails[-1][1])

    response = await client.post(RESEND_URL, json={"email": email})
    assert response.status_code == 202
    assert len(sent_emails) == 2
    new_token = _token_from(sent_emails[-1][1])

    assert (await client.post(VERIFY_URL, json={"token": old_token})).status_code == 400
    assert (await client.post(VERIFY_URL, json={"token": new_token})).status_code == 204


@pytest.mark.asyncio
@pytest.mark.usefixtures("verification_required")
async def test_resend_answers_the_same_and_sends_only_when_needed(
    client: AsyncClient, sent_emails: list[tuple[str, str]]
) -> None:
    email = "verify_resend_same@example.com"
    await _register(client, email)
    sent_emails.clear()

    unknown = await client.post(RESEND_URL, json={"email": "nobody_here@example.com"})
    # Within the cooldown after the registration email nothing new is sent
    too_soon = await client.post(RESEND_URL, json={"email": email})

    assert unknown.status_code == too_soon.status_code == 202
    assert unknown.json() == too_soon.json()
    assert sent_emails == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("verification_required", "no_cooldown")
async def test_no_link_for_verified_account(
    client: AsyncClient, sent_emails: list[tuple[str, str]]
) -> None:
    email = "verify_done@example.com"
    await _register(client, email)
    await client.post(VERIFY_URL, json={"token": _token_from(sent_emails[-1][1])})
    sent_emails.clear()

    response = await client.post(RESEND_URL, json={"email": email})

    assert response.status_code == 202
    assert sent_emails == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("verification_required")
async def test_password_reset_confirms_the_email(
    client: AsyncClient,
    db_session: AsyncSession,
    sent_emails: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = "verify_by_reset@example.com"
    await _register(client, email)
    reset_links: list[str] = []
    monkeypatch.setattr(
        "app.api.v1.auth.send_password_reset_email", lambda _to, url: reset_links.append(url)
    )

    await client.post("/api/v1/auth/forgot-password", json={"email": email})
    response = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": _token_from(reset_links[-1]), "new_password": "N3wStr0ngPass!"},
    )
    assert response.status_code == 204

    user = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
    await db_session.refresh(user)
    assert user.is_verified is True


@pytest.mark.asyncio
@pytest.mark.usefixtures("verification_required", "no_cooldown")
async def test_daily_limit_counts_the_registration_email(
    client: AsyncClient, sent_emails: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "EMAIL_VERIFICATION_MAX_PER_DAY", 3)
    email = "verify_daily_limit@example.com"
    await _register(client, email)

    answers = [(await client.post(RESEND_URL, json={"email": email})).json() for _ in range(4)]

    # Registration email and two more; later requests get the same answer, without an email
    assert len(sent_emails) == 3
    assert all(answer == answers[0] for answer in answers)
