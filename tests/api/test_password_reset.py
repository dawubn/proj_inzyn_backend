from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password_reset_token
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User

PASSWORD = "Str0ngPass!"
NEW_PASSWORD = "N3wStr0ngPass!"
FORGOT_URL = "/api/v1/auth/forgot-password"
RESET_URL = "/api/v1/auth/reset-password"
LOGIN_URL = "/api/v1/auth/login"


@pytest.fixture
def sent_emails(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Collects (recipient, reset link) instead of sending real emails."""
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.api.v1.auth.send_password_reset_email",
        lambda to, reset_url: sent.append((to, reset_url)),
    )
    return sent


@pytest.fixture
def no_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "PASSWORD_RESET_REQUEST_COOLDOWN_SECONDS", 0)


async def _register(client: AsyncClient, email: str) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "name": "Reset", "surname": "User"},
    )
    assert response.status_code == 201


def _token_from(reset_url: str) -> str:
    return parse_qs(urlparse(reset_url).query)["token"][0]


async def _request_token(
    client: AsyncClient, sent_emails: list[tuple[str, str]], email: str
) -> str:
    response = await client.post(FORGOT_URL, json={"email": email})
    assert response.status_code == 202
    return _token_from(sent_emails[-1][1])


@pytest.mark.asyncio
async def test_forgot_password_sends_reset_link(
    client: AsyncClient, sent_emails: list[tuple[str, str]]
) -> None:
    await _register(client, "reset_link@example.com")

    response = await client.post(FORGOT_URL, json={"email": "reset_link@example.com"})

    assert response.status_code == 202
    assert len(sent_emails) == 1
    recipient, reset_url = sent_emails[0]
    assert recipient == "reset_link@example.com"
    assert reset_url.startswith(f"{settings.FRONTEND_URL}/reset-password?token=")


@pytest.mark.asyncio
async def test_forgot_password_does_not_reveal_unknown_email(
    client: AsyncClient, sent_emails: list[tuple[str, str]]
) -> None:
    await _register(client, "reset_known@example.com")

    known = await client.post(FORGOT_URL, json={"email": "reset_known@example.com"})
    unknown = await client.post(FORGOT_URL, json={"email": "reset_unknown@example.com"})

    assert unknown.status_code == known.status_code == 202
    assert unknown.json() == known.json()
    assert [recipient for recipient, _ in sent_emails] == ["reset_known@example.com"]


@pytest.mark.asyncio
async def test_forgot_password_skips_deactivated_account(
    client: AsyncClient, db_session: AsyncSession, sent_emails: list[tuple[str, str]]
) -> None:
    await _register(client, "reset_inactive@example.com")
    await db_session.execute(
        update(User).where(User.email == "reset_inactive@example.com").values(is_active=False)
    )

    response = await client.post(FORGOT_URL, json={"email": "reset_inactive@example.com"})

    assert response.status_code == 202
    assert sent_emails == []


@pytest.mark.asyncio
async def test_forgot_password_has_cooldown(
    client: AsyncClient, sent_emails: list[tuple[str, str]]
) -> None:
    await _register(client, "reset_cooldown@example.com")

    first = await client.post(FORGOT_URL, json={"email": "reset_cooldown@example.com"})
    second = await client.post(FORGOT_URL, json={"email": "reset_cooldown@example.com"})

    assert first.status_code == second.status_code == 202
    assert len(sent_emails) == 1


@pytest.mark.asyncio
async def test_reset_token_is_stored_only_as_hash(
    client: AsyncClient, db_session: AsyncSession, sent_emails: list[tuple[str, str]]
) -> None:
    await _register(client, "reset_hash@example.com")
    token = await _request_token(client, sent_emails, "reset_hash@example.com")

    stored = (
        await db_session.execute(
            select(PasswordResetToken.token_hash)
            .join(User, User.id == PasswordResetToken.user_id)
            .where(User.email == "reset_hash@example.com")
        )
    ).scalar_one()

    assert stored != token
    assert stored == hash_password_reset_token(token)


@pytest.mark.asyncio
async def test_reset_password_changes_password(
    client: AsyncClient, sent_emails: list[tuple[str, str]]
) -> None:
    email = "reset_flow@example.com"
    await _register(client, email)
    token = await _request_token(client, sent_emails, email)

    response = await client.post(RESET_URL, json={"token": token, "new_password": NEW_PASSWORD})
    assert response.status_code == 204

    old_login = await client.post(LOGIN_URL, json={"email": email, "password": PASSWORD})
    assert old_login.status_code == 401
    new_login = await client.post(LOGIN_URL, json={"email": email, "password": NEW_PASSWORD})
    assert new_login.status_code == 200

    me = await client.get("/api/v1/users/me")
    assert me.status_code == 200
    assert me.json()["email"] == email


@pytest.mark.asyncio
async def test_reset_link_works_only_once(
    client: AsyncClient, sent_emails: list[tuple[str, str]]
) -> None:
    email = "reset_once@example.com"
    await _register(client, email)
    token = await _request_token(client, sent_emails, email)

    first = await client.post(RESET_URL, json={"token": token, "new_password": NEW_PASSWORD})
    second = await client.post(RESET_URL, json={"token": token, "new_password": "An0therPass!"})

    assert first.status_code == 204
    assert second.status_code == 400
    login = await client.post(LOGIN_URL, json={"email": email, "password": NEW_PASSWORD})
    assert login.status_code == 200


@pytest.mark.asyncio
async def test_reset_password_rejects_expired_link(
    client: AsyncClient, db_session: AsyncSession, sent_emails: list[tuple[str, str]]
) -> None:
    email = "reset_expired@example.com"
    await _register(client, email)
    token = await _request_token(client, sent_emails, email)
    await db_session.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.token_hash == hash_password_reset_token(token))
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )

    response = await client.post(RESET_URL, json={"token": token, "new_password": NEW_PASSWORD})

    assert response.status_code == 400
    login = await client.post(LOGIN_URL, json={"email": email, "password": PASSWORD})
    assert login.status_code == 200


@pytest.mark.asyncio
async def test_reset_password_rejects_unknown_token(client: AsyncClient) -> None:
    response = await client.post(
        RESET_URL, json={"token": "not-a-real-token", "new_password": NEW_PASSWORD}
    )

    assert response.status_code == 400


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_cooldown")
async def test_new_reset_request_invalidates_previous_link(
    client: AsyncClient, sent_emails: list[tuple[str, str]]
) -> None:
    email = "reset_newer@example.com"
    await _register(client, email)
    old_token = await _request_token(client, sent_emails, email)
    new_token = await _request_token(client, sent_emails, email)
    assert old_token != new_token

    old = await client.post(RESET_URL, json={"token": old_token, "new_password": NEW_PASSWORD})
    new = await client.post(RESET_URL, json={"token": new_token, "new_password": NEW_PASSWORD})

    assert old.status_code == 400
    assert new.status_code == 204


@pytest.mark.asyncio
@pytest.mark.parametrize("new_password", ["short", "x" * 73])
async def test_reset_password_validates_new_password(
    client: AsyncClient, sent_emails: list[tuple[str, str]], new_password: str
) -> None:
    email = f"reset_invalid_{len(new_password)}@example.com"
    await _register(client, email)
    token = await _request_token(client, sent_emails, email)

    response = await client.post(RESET_URL, json={"token": token, "new_password": new_password})

    assert response.status_code == 422
    # The link is still usable after a rejected attempt
    retry = await client.post(RESET_URL, json={"token": token, "new_password": NEW_PASSWORD})
    assert retry.status_code == 204


@pytest.mark.asyncio
async def test_reset_password_ends_existing_sessions(
    client: AsyncClient, sent_emails: list[tuple[str, str]]
) -> None:
    email = "reset_sessions@example.com"
    await _register(client, email)
    login = await client.post(LOGIN_URL, json={"email": email, "password": PASSWORD})
    old_tokens = login.json()
    old_auth = {"Authorization": f"Bearer {old_tokens['access_token']}"}
    assert (await client.get("/api/v1/users/me", headers=old_auth)).status_code == 200

    token = await _request_token(client, sent_emails, email)
    response = await client.post(RESET_URL, json={"token": token, "new_password": NEW_PASSWORD})
    assert response.status_code == 204

    # Cookies of this browser are cleared, and the old tokens no longer work anywhere
    assert "access_token" not in client.cookies
    assert (await client.get("/api/v1/users/me", headers=old_auth)).status_code == 401
    refresh = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": old_tokens["refresh_token"]}
    )
    assert refresh.status_code == 401

    new_login = await client.post(LOGIN_URL, json={"email": email, "password": NEW_PASSWORD})
    new_tokens = new_login.json()
    new_auth = {"Authorization": f"Bearer {new_tokens['access_token']}"}
    assert (await client.get("/api/v1/users/me", headers=new_auth)).status_code == 200
    new_refresh = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": new_tokens["refresh_token"]}
    )
    assert new_refresh.status_code == 204


CHANGE_URL = "/api/v1/users/me/password"


async def _register_and_login(client: AsyncClient, email: str) -> dict[str, str]:
    await _register(client, email)
    login = await client.post(LOGIN_URL, json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    tokens: dict[str, str] = login.json()
    return tokens


@pytest.mark.asyncio
async def test_change_password(client: AsyncClient) -> None:
    email = "change_ok@example.com"
    await _register_and_login(client, email)

    response = await client.post(
        CHANGE_URL, json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    )

    assert response.status_code == 204
    # The session of this browser continues with the new cookies
    assert (await client.get("/api/v1/users/me")).status_code == 200

    client.cookies.clear()
    old_login = await client.post(LOGIN_URL, json={"email": email, "password": PASSWORD})
    assert old_login.status_code == 401
    new_login = await client.post(LOGIN_URL, json={"email": email, "password": NEW_PASSWORD})
    assert new_login.status_code == 200


@pytest.mark.asyncio
async def test_change_password_requires_current_password(client: AsyncClient) -> None:
    email = "change_wrong@example.com"
    await _register_and_login(client, email)

    response = await client.post(
        CHANGE_URL, json={"current_password": "Wr0ngPass!", "new_password": NEW_PASSWORD}
    )

    assert response.status_code == 400
    assert (await client.get("/api/v1/users/me")).status_code == 200
    client.cookies.clear()
    login = await client.post(LOGIN_URL, json={"email": email, "password": PASSWORD})
    assert login.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("new_password", ["short", "x" * 73, PASSWORD])
async def test_change_password_validates_new_password(
    client: AsyncClient, new_password: str
) -> None:
    await _register_and_login(client, f"change_invalid_{len(new_password)}@example.com")

    response = await client.post(
        CHANGE_URL, json={"current_password": PASSWORD, "new_password": new_password}
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_change_password_ends_other_sessions(client: AsyncClient) -> None:
    email = "change_sessions@example.com"
    other_device = await _register_and_login(client, email)
    other_auth = {"Authorization": f"Bearer {other_device['access_token']}"}
    await client.post(LOGIN_URL, json={"email": email, "password": PASSWORD})

    response = await client.post(
        CHANGE_URL, json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    )
    assert response.status_code == 204

    client.cookies.clear()
    assert (await client.get("/api/v1/users/me", headers=other_auth)).status_code == 401
    refresh = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": other_device["refresh_token"]}
    )
    assert refresh.status_code == 401


@pytest.mark.asyncio
async def test_change_password_invalidates_pending_reset_link(
    client: AsyncClient, sent_emails: list[tuple[str, str]]
) -> None:
    email = "change_reset_link@example.com"
    await _register_and_login(client, email)
    token = await _request_token(client, sent_emails, email)

    changed = await client.post(
        CHANGE_URL, json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    )
    assert changed.status_code == 204

    response = await client.post(RESET_URL, json={"token": token, "new_password": "An0therPass!"})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_change_password_requires_login(client: AsyncClient) -> None:
    client.cookies.clear()

    response = await client.post(
        CHANGE_URL, json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    )

    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_cooldown")
async def test_daily_limit_of_reset_emails(
    client: AsyncClient, sent_emails: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "PASSWORD_RESET_MAX_PER_DAY", 2)
    email = "reset_daily_limit@example.com"
    await _register(client, email)

    statuses = [
        (await client.post(FORGOT_URL, json={"email": email})).status_code for _ in range(4)
    ]

    assert statuses == [202, 202, 202, 202]
    assert len(sent_emails) == 2
