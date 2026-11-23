import smtplib
from email.message import EmailMessage
from typing import Any

import pytest

from app.core.config import settings
from app.services import email as email_service


class _FakeSMTP:
    instances: list["_FakeSMTP"] = []
    fail_on_send = False

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.host = host
        self.port = port
        self.started_tls = False
        self.credentials: tuple[str, str] | None = None
        self.messages: list[EmailMessage] = []
        _FakeSMTP.instances.append(self)

    def __enter__(self) -> "_FakeSMTP":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        self.credentials = (username, password)

    def send_message(self, message: EmailMessage) -> None:
        if _FakeSMTP.fail_on_send:
            raise smtplib.SMTPException("server unavailable")
        self.messages.append(message)


@pytest.fixture
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> type[_FakeSMTP]:
    _FakeSMTP.instances = []
    _FakeSMTP.fail_on_send = False
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "SMTP_PORT", 587)
    monkeypatch.setattr(settings, "SMTP_USERNAME", "mailer")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "secret")
    monkeypatch.setattr(settings, "SMTP_USE_TLS", True)
    monkeypatch.setattr(settings, "SMTP_FROM", "CerberDoc <no-reply@example.com>")
    return _FakeSMTP


def test_password_reset_email_is_sent_over_smtp(fake_smtp: type[_FakeSMTP]) -> None:
    reset_url = "https://app.example.com/reset-password?token=abc"

    email_service.send_password_reset_email("user@example.com", reset_url)

    assert len(fake_smtp.instances) == 1
    smtp = fake_smtp.instances[0]
    assert (smtp.host, smtp.port) == ("smtp.example.com", 587)
    assert smtp.started_tls
    assert smtp.credentials == ("mailer", "secret")
    message = smtp.messages[0]
    assert message["To"] == "user@example.com"
    assert message["From"] == "CerberDoc <no-reply@example.com>"
    assert reset_url in message.get_content()


def test_email_is_not_sent_without_smtp_host(
    fake_smtp: type[_FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "SMTP_HOST", None)

    email_service.send_email("user@example.com", "Subject", "Body")

    assert fake_smtp.instances == []


def test_smtp_failure_is_not_raised(fake_smtp: type[_FakeSMTP]) -> None:
    fake_smtp.fail_on_send = True

    email_service.send_email("user@example.com", "Subject", "Body")

    assert fake_smtp.instances[0].messages == []


def test_reply_to_is_set_when_given(fake_smtp: type[_FakeSMTP]) -> None:
    email_service.send_email("team@example.com", "Subject", "Body", reply_to="sender@example.com")

    [message] = fake_smtp.instances[0].messages
    assert message["Reply-To"] == "sender@example.com"
