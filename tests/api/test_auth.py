import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_register_user(client: AsyncClient) -> None:
    payload = {
        "email": "test@example.com",
        "password": "Str0ngPass!",
        "full_name": "Test User",
        "role": "business_user",
    }
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == payload["email"]
    assert "id" in data
    assert "hashed_password" not in data


@pytest.mark.asyncio
async def test_register_duplicate_email(client: AsyncClient) -> None:
    payload = {
        "email": "duplicate@example.com",
        "password": "Str0ngPass!",
        "full_name": "User One",
        "role": "business_user",
    }
    await client.post("/api/v1/auth/register", json=payload)
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient) -> None:
    reg_payload = {
        "email": "login_test@example.com",
        "password": "Str0ngPass!",
        "full_name": "Login User",
        "role": "business_user",
    }
    await client.post("/api/v1/auth/register", json=reg_payload)

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": reg_payload["email"], "password": reg_payload["password"]},
    )
    assert response.status_code == 200
    # Check user info in response body
    data = response.json()
    assert data["email"] == reg_payload["email"]
    assert "id" in data
    # Check secure cookies
    assert "access_token" in client.cookies
    assert "refresh_token" in client.cookies
    # Verify cookie properties
    cookies = response.cookies
    assert cookies["access_token"]  # Should be set
    assert cookies["refresh_token"]  # Should be set


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "nonexistent@example.com", "password": "wrong"},
    )
    assert response.status_code == 401
