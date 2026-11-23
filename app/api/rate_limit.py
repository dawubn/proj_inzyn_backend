"""Limits of requests per client IP address for the public auth endpoints.

Counters live in Redis, so every API worker shares them. The client address is
`request.client`, which uvicorn takes from X-Forwarded-For only for proxies listed in
FORWARDED_ALLOW_IPS; behind Cloudflare it is CF-Connecting-IP (see README,
"Request limits per IP").
"""

import ipaddress
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog
from fastapi import Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.exceptions import TooManyRequestsError

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class Limit:
    name: str
    requests: int
    seconds: int


# Generous for a person, low enough to stop guessing passwords or sending mass emails
LOGIN = Limit("login", requests=10, seconds=60)
REGISTER = Limit("register", requests=5, seconds=3600)
FORGOT_PASSWORD = Limit("forgot_password", requests=10, seconds=3600)
RESET_PASSWORD = Limit("reset_password", requests=20, seconds=3600)
# For the email verification endpoints (verify-email, resend-verification)
VERIFY_EMAIL = Limit("verify_email", requests=20, seconds=3600)
RESEND_VERIFICATION = Limit("resend_verification", requests=10, seconds=3600)
# A person rarely needs more than a couple of messages a day
CONTACT = Limit("contact", requests=settings.CONTACT_MAX_PER_IP_PER_DAY, seconds=24 * 3600)

# OpenAPI description of the answer to a request over the limit
TOO_MANY_REQUESTS = {"description": "Too many requests from this IP address; see Retry-After"}

_redis: Redis | None = None


def _client() -> Redis:
    global _redis  # noqa: PLW0603
    if _redis is None:
        _redis = Redis.from_url(settings.REDIS_URL, socket_timeout=1, socket_connect_timeout=1)
    return _redis


async def count_request(key: str, seconds: int) -> int:
    """Count a request in a fixed time window and return how many there were in it."""
    async with _client().pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.expire(key, seconds)
        count, _ = await pipe.execute()
    return int(count)


# One IPv6 connection usually gets a whole /64 network, so its addresses count as one
_IPV6_PREFIX = 64


def _from_cloudflare(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in ipaddress.ip_network(net) for net in settings.CLOUDFLARE_IP_RANGES)


def _counted_address(address: str) -> str | None:
    """The address as counted: IPv4 as is, IPv6 as its /64 network; None when not an address."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address):
        return str(ipaddress.ip_network(f"{ip}/{_IPV6_PREFIX}", strict=False))
    return str(ip)


def client_ip(request: Request) -> str:
    """Address of the client the limits are counted for.

    CF-Connecting-IP is used only for requests that really come from Cloudflare;
    from anyone else the header could be faked.
    """
    peer = request.client.host if request.client else "unknown"
    connecting = request.headers.get("cf-connecting-ip")
    if connecting and _from_cloudflare(peer):
        from_header = _counted_address(connecting.strip())
        if from_header:
            return from_header
    return _counted_address(peer) or peer


def rate_limit(limit: Limit) -> Callable[[Request], Awaitable[None]]:
    """FastAPI dependency that answers 429 when an IP address goes over the limit."""

    async def check(request: Request) -> None:
        if not settings.RATE_LIMIT_ENABLED:
            return

        ip = client_ip(request)
        window = int(time.time()) // limit.seconds
        try:
            count = await count_request(f"rate:{limit.name}:{ip}:{window}", limit.seconds)
        except RedisError:
            # Better to let people log in than to lock everyone out when Redis is down
            logger.warning("Rate limit not checked, Redis unavailable", limit=limit.name)
            return

        if count > limit.requests:
            retry_after = limit.seconds - int(time.time()) % limit.seconds
            logger.warning("Rate limit exceeded", limit=limit.name, ip=ip)
            raise TooManyRequestsError(retry_after)

    return check
