from collections import Counter
from collections.abc import Callable

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from main import app
from redis.exceptions import ConnectionError as RedisConnectionError

from app.api import rate_limit
from app.core.config import settings

LOGIN_URL = "/api/v1/auth/login"
CREDENTIALS = {"email": "nobody@example.com", "password": "Wr0ngPass!"}


@pytest.fixture
def counters(monkeypatch: pytest.MonkeyPatch) -> Counter[str]:
    """Request limits on, with counters in memory instead of Redis."""
    counts: Counter[str] = Counter()

    async def count_request(key: str, _seconds: int) -> int:
        counts[key] += 1
        return counts[key]

    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(rate_limit, "count_request", count_request)
    return counts


ClientFrom = Callable[[str], AsyncClient]


@pytest.fixture
def client_from(client: AsyncClient) -> ClientFrom:
    """Clients that connect from a chosen IP address; `client` sets up the test database."""

    def make(ip: str) -> AsyncClient:
        return AsyncClient(
            transport=ASGITransport(app=app, client=(ip, 50000)), base_url="http://test"
        )

    return make


@pytest.mark.asyncio
@pytest.mark.usefixtures("counters")
async def test_login_answers_429_after_the_limit(client_from: ClientFrom) -> None:
    async with client_from("203.0.113.10") as client:
        statuses = [
            (await client.post(LOGIN_URL, json=CREDENTIALS)).status_code
            for _ in range(rate_limit.LOGIN.requests + 1)
        ]
        over = await client.post(LOGIN_URL, json=CREDENTIALS)

    assert statuses[:-1] == [401] * rate_limit.LOGIN.requests
    assert statuses[-1] == 429
    assert over.json()["code"] == "too_many_requests"
    assert 0 < int(over.headers["Retry-After"]) <= rate_limit.LOGIN.seconds


@pytest.mark.asyncio
@pytest.mark.usefixtures("counters")
async def test_each_ip_address_has_its_own_limit(client_from: ClientFrom) -> None:
    async with client_from("203.0.113.20") as first, client_from("203.0.113.21") as second:
        for _ in range(rate_limit.LOGIN.requests):
            await first.post(LOGIN_URL, json=CREDENTIALS)

        assert (await first.post(LOGIN_URL, json=CREDENTIALS)).status_code == 429
        assert (await second.post(LOGIN_URL, json=CREDENTIALS)).status_code == 401


@pytest.mark.asyncio
async def test_requests_pass_when_redis_is_down(
    client_from: ClientFrom, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(_key: str, _seconds: int) -> int:
        raise RedisConnectionError("down")

    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(rate_limit, "count_request", broken)

    async with client_from("203.0.113.30") as client:
        statuses = {
            (await client.post(LOGIN_URL, json=CREDENTIALS)).status_code
            for _ in range(rate_limit.LOGIN.requests + 2)
        }

    assert statuses == {401}


@pytest.mark.asyncio
@pytest.mark.usefixtures("counters")
async def test_limits_are_counted_per_endpoint(client_from: ClientFrom) -> None:
    async with client_from("203.0.113.40") as client:
        for _ in range(rate_limit.LOGIN.requests + 1):
            await client.post(LOGIN_URL, json=CREDENTIALS)

        response = await client.post(
            "/api/v1/auth/forgot-password", json={"email": "nobody@example.com"}
        )

    assert response.status_code == 202


@pytest.mark.parametrize(
    ("peer", "header", "counted"),
    [
        # Request from a Cloudflare server: the client address comes from the header
        ("172.70.1.1", "203.0.113.50", "203.0.113.50"),
        # Anyone else cannot set the address with the header
        ("198.51.100.9", "203.0.113.50", "198.51.100.9"),
        ("198.51.100.9", None, "198.51.100.9"),
        # A broken header from Cloudflare falls back to the connecting address
        ("172.70.1.1", "not-an-ip", "172.70.1.1"),
        # Addresses of one IPv6 connection count together
        ("2001:db8:1:2:aaaa::1", None, "2001:db8:1:2::/64"),
        ("2606:4700::1", "2001:db8:1:2:bbbb::7", "2001:db8:1:2::/64"),
    ],
)
def test_client_address(peer: str, header: str | None, counted: str) -> None:
    headers = [(b"cf-connecting-ip", header.encode())] if header else []
    request = Request({"type": "http", "client": (peer, 443), "headers": headers})

    assert rate_limit.client_ip(request) == counted


@pytest.mark.asyncio
@pytest.mark.usefixtures("counters")
async def test_users_behind_cloudflare_have_separate_limits(client_from: ClientFrom) -> None:
    async with client_from("172.70.1.1") as cloudflare:
        for _ in range(rate_limit.LOGIN.requests + 1):
            await cloudflare.post(
                LOGIN_URL, json=CREDENTIALS, headers={"CF-Connecting-IP": "203.0.113.60"}
            )
        other = await cloudflare.post(
            LOGIN_URL, json=CREDENTIALS, headers={"CF-Connecting-IP": "203.0.113.61"}
        )

    assert other.status_code == 401
