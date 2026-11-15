import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_register_user(client: AsyncClient) -> None:
    payload = {
        "email": "test@example.com",
        "password": "Str0ngPass!",
        "name": "Test",
        "surname": "User",
        "role": "business_user",
    }
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == payload["email"]
    assert data["name"] == "Test"
    assert data["surname"] == "User"
    assert data["company"] is None
    assert "id" in data
    assert "hashed_password" not in data


@pytest.mark.asyncio
async def test_register_user_with_company(client: AsyncClient) -> None:
    payload = {
        "email": "company@example.com",
        "password": "Str0ngPass!",
        "name": "Company",
        "surname": "User",
        "company": "Acme",
    }
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201
    assert response.json()["company"] == "Acme"


@pytest.mark.asyncio
async def test_register_requires_surname(client: AsyncClient) -> None:
    payload = {
        "email": "nosurname@example.com",
        "password": "Str0ngPass!",
        "name": "Solo",
    }
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_register_duplicate_email(client: AsyncClient) -> None:
    payload = {
        "email": "duplicate@example.com",
        "password": "Str0ngPass!",
        "name": "User",
        "surname": "One",
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
        "name": "Login",
        "surname": "User",
        "role": "business_user",
    }
    await client.post("/api/v1/auth/register", json=reg_payload)

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": reg_payload["email"], "password": reg_payload["password"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    # Cookies should also be set for browser clients
    assert "access_token" in client.cookies
    assert "refresh_token" in client.cookies


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "nonexistent@example.com", "password": "wrong"},
    )
    assert response.status_code == 401
