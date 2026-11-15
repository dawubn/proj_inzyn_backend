import pytest
from httpx import AsyncClient


async def _login(client: AsyncClient, email: str, company: str | None = None) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "Str0ngPass!",
            "name": "Profile",
            "surname": "User",
            "company": company,
        },
    )
    await client.post("/api/v1/auth/login", json={"email": email, "password": "Str0ngPass!"})


@pytest.mark.asyncio
async def test_update_me_changes_profile(client: AsyncClient) -> None:
    await _login(client, "profile_update@example.com")

    response = await client.patch(
        "/api/v1/users/me",
        json={"name": " Anna ", "surname": "Nowak", "company": "Acme"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Anna"
    assert data["surname"] == "Nowak"
    assert data["company"] == "Acme"

    me = await client.get("/api/v1/users/me")
    assert me.json()["name"] == "Anna"
    assert me.json()["company"] == "Acme"


@pytest.mark.asyncio
async def test_update_me_keeps_omitted_fields(client: AsyncClient) -> None:
    await _login(client, "profile_partial@example.com", company="Acme")

    response = await client.patch("/api/v1/users/me", json={"surname": "Changed"})

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Profile"
    assert data["surname"] == "Changed"
    assert data["company"] == "Acme"


@pytest.mark.asyncio
async def test_update_me_clears_company(client: AsyncClient) -> None:
    await _login(client, "profile_company@example.com", company="Acme")

    response = await client.patch("/api/v1/users/me", json={"company": None})

    assert response.status_code == 200
    assert response.json()["company"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"name": ""},
        {"name": "   "},
        {"name": None},
        {"surname": None},
        {"name": "x" * 256},
    ],
)
async def test_update_me_rejects_invalid_values(
    client: AsyncClient, payload: dict[str, str | None]
) -> None:
    await _login(client, "profile_invalid@example.com")

    response = await client.patch("/api/v1/users/me", json=payload)

    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"email": "other@example.com"},
        {"role": "admin"},
        {"is_active": False},
    ],
)
async def test_update_me_rejects_fields_outside_profile(
    client: AsyncClient, payload: dict[str, str | bool]
) -> None:
    await _login(client, "profile_forbidden@example.com")

    response = await client.patch("/api/v1/users/me", json=payload)

    assert response.status_code == 422
    me = await client.get("/api/v1/users/me")
    assert me.json()["email"] == "profile_forbidden@example.com"
    assert me.json()["role"] == "business_user"


@pytest.mark.asyncio
async def test_update_me_requires_login(client: AsyncClient) -> None:
    client.cookies.clear()

    response = await client.patch("/api/v1/users/me", json={"name": "Anna"})

    assert response.status_code == 401
