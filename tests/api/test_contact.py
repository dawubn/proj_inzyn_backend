from collections import Counter
from typing import Any

import pytest
from httpx import AsyncClient

from app.api import rate_limit
from app.core.config import settings
from app.schemas.contact import MESSAGE_MAX_LENGTH

URL = "/api/v1/contact"
RIGHT_TO_LEFT_OVERRIDE = chr(0x202E)
TEAM = "team@example.com"
MESSAGE = {
    "email": "sender@example.com",
    "name": "Jan Kowalski",
    "subject": "Question about my account",
    "message": "Hello, how can I change my company name?",
}


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Emails sent by the contact form, with the address configured."""
    emails: list[dict[str, Any]] = []

    def send_email(to: str, subject: str, body: str, reply_to: str | None = None) -> None:
        emails.append({"to": to, "subject": subject, "body": body, "reply_to": reply_to})

    monkeypatch.setattr(settings, "CONTACT_EMAIL", TEAM)
    monkeypatch.setattr("app.services.contact.send_email", send_email)
    return emails


@pytest.fixture
def counters(monkeypatch: pytest.MonkeyPatch) -> Counter[str]:
    counts: Counter[str] = Counter()

    async def count_request(key: str, _seconds: int) -> int:
        counts[key] += 1
        return counts[key]

    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(rate_limit, "count_request", count_request)
    return counts


@pytest.mark.asyncio
async def test_message_is_emailed_to_the_team(
    client: AsyncClient, sent: list[dict[str, Any]]
) -> None:
    response = await client.post(URL, json=MESSAGE)

    assert response.status_code == 202
    assert TEAM not in response.text
    [email] = sent
    assert email["to"] == TEAM
    assert email["reply_to"] == "sender@example.com"
    assert email["subject"] == "[CerberDoc contact] Question about my account"
    assert "Jan Kowalski <sender@example.com>" in email["body"]
    assert MESSAGE["message"] in email["body"]


@pytest.mark.asyncio
async def test_name_and_subject_are_optional(
    client: AsyncClient, sent: list[dict[str, Any]]
) -> None:
    response = await client.post(
        URL, json={"email": "sender@example.com", "message": "Just a short hello."}
    )

    assert response.status_code == 202
    assert sent[0]["subject"] == "[CerberDoc contact] Message from the contact form"


@pytest.mark.asyncio
async def test_filled_hidden_field_looks_sent_but_is_dropped(
    client: AsyncClient, sent: list[dict[str, Any]]
) -> None:
    response = await client.post(URL, json={**MESSAGE, "website": "http://spam.example"})

    assert response.status_code == 202
    assert sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"email": "not-an-email"},
        {"email": "a\nBcc: other@example.com"},
        {"subject": "Hello\r\nBcc: other@example.com"},
        {"name": "Jan\nKowalski"},
        {"subject": "x" * 151},
        {"name": "x" * 101},
        {"message": "   too short   "},
        {"message": "\u200b" * 20},
        {"message": "x" * (MESSAGE_MAX_LENGTH + 1)},
        {"message": "See http://a.example http://b.example www.c.example https://d.example"},
        {"unexpected": "field"},
    ],
)
async def test_invalid_input_is_rejected(
    client: AsyncClient, sent: list[dict[str, Any]], change: dict[str, str]
) -> None:
    response = await client.post(URL, json={**MESSAGE, **change})

    assert response.status_code == 422
    assert sent == []


@pytest.mark.asyncio
async def test_hidden_characters_are_removed(
    client: AsyncClient, sent: list[dict[str, Any]]
) -> None:
    await client.post(
        URL,
        json={
            **MESSAGE,
            "subject": f"Invoice{RIGHT_TO_LEFT_OVERRIDE} fdp.exe",
            "message": "Hello\u200b team,\r\n\r\n\r\n\r\nplease help\u0007.",
        },
    )

    assert sent[0]["subject"] == "[CerberDoc contact] Invoice fdp.exe"
    assert "Hello team,\n\nplease help." in sent[0]["body"]


@pytest.mark.asyncio
async def test_without_configured_address_the_form_is_unavailable(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "CONTACT_EMAIL", None)

    response = await client.post(URL, json=MESSAGE)

    assert response.status_code == 503
    assert response.json()["code"] == "contact_unavailable"


@pytest.mark.asyncio
@pytest.mark.usefixtures("counters")
async def test_messages_per_ip_are_limited_per_day(
    client: AsyncClient, sent: list[dict[str, Any]]
) -> None:
    responses = [await client.post(URL, json=MESSAGE) for _ in range(4)]

    assert rate_limit.CONTACT.requests == 3
    assert rate_limit.CONTACT.seconds == 24 * 3600
    assert [r.status_code for r in responses] == [202, 202, 202, 429]
    assert 0 < int(responses[-1].headers["Retry-After"]) <= 24 * 3600
    assert len(sent) == 3


@pytest.mark.asyncio
async def test_messages_from_everyone_are_limited_per_day(
    client: AsyncClient,
    sent: list[dict[str, Any]],
    counters: Counter[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "CONTACT_MAX_PER_DAY", 1)

    statuses = [(await client.post(URL, json=MESSAGE)).status_code for _ in range(2)]

    # The second message is still within the limit of one IP, so the daily total stops it
    assert statuses == [202, 429]
    assert len(sent) == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("counters")
async def test_browser_can_read_retry_after(
    client: AsyncClient, sent: list[dict[str, Any]]
) -> None:
    allowed = settings.APP_ALLOWED_HOSTS
    origin = "http://localhost:5173" if "*" in allowed else allowed[0]
    for _ in range(rate_limit.CONTACT.requests):
        await client.post(URL, json=MESSAGE, headers={"Origin": origin})

    response = await client.post(URL, json=MESSAGE, headers={"Origin": origin})

    assert response.status_code == 429
    assert "retry-after" in response.headers["access-control-expose-headers"].lower()
