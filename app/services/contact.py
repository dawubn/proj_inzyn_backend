from datetime import UTC, datetime

import structlog
from redis.exceptions import RedisError

from app.api import rate_limit
from app.core.config import settings
from app.core.exceptions import ContactUnavailableError, TooManyRequestsError
from app.schemas.contact import ContactRequest
from app.services.email import send_email

logger = structlog.get_logger(__name__)

_DAY_SECONDS = 24 * 60 * 60


def contact_recipient() -> str:
    """Address that receives the messages; it is never sent to the browser."""
    if not settings.CONTACT_EMAIL:
        raise ContactUnavailableError()
    return settings.CONTACT_EMAIL


async def check_daily_limit() -> None:
    """Cap of messages a day from all addresses together, so the inbox cannot be flooded."""
    if not settings.RATE_LIMIT_ENABLED:
        return
    day = int(datetime.now(UTC).timestamp()) // _DAY_SECONDS
    try:
        count = await rate_limit.count_request(f"rate:contact_all:{day}", _DAY_SECONDS)
    except RedisError:
        logger.warning("Contact daily limit not checked, Redis unavailable")
        return
    if count > settings.CONTACT_MAX_PER_DAY:
        logger.warning("Contact form daily limit reached", limit=settings.CONTACT_MAX_PER_DAY)
        seconds_left = _DAY_SECONDS - int(datetime.now(UTC).timestamp()) % _DAY_SECONDS
        raise TooManyRequestsError(seconds_left)


def build_contact_email(data: ContactRequest) -> tuple[str, str]:
    """Subject and plain-text body of the email the team receives."""
    subject = f"[CerberDoc contact] {data.subject or 'Message from the contact form'}"
    body = (
        "New message from the CerberDoc contact form.\n\n"
        f"From: {data.name or '(no name)'} <{data.email}>\n"
        f"Subject: {data.subject or '(none)'}\n"
        f"Sent: {datetime.now(UTC):%Y-%m-%d %H:%M} UTC\n\n"
        "----------------------------------------\n"
        f"{data.message}\n"
        "----------------------------------------\n\n"
        "Reply to this email to answer the sender. The address was typed into the form "
        "and has not been verified, so do not send account details or links to it "
        "without checking who you are writing to.\n"
    )
    return subject, body


def send_contact_message(to: str, data: ContactRequest) -> None:
    subject, body = build_contact_email(data)
    send_email(to, subject, body, reply_to=str(data.email))
