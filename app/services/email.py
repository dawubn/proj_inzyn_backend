import smtplib
from email.message import EmailMessage

import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

_SMTP_TIMEOUT_SECONDS = 15


def send_email(to: str, subject: str, body: str) -> None:
    """Send a plain-text email. Failures are logged, never raised to the caller."""
    if not settings.SMTP_HOST:
        if settings.APP_ENV == "production":
            logger.error("Email not sent: SMTP is not configured", subject=subject)
        else:
            # Development helper: lets the flow be tested without a mail server
            logger.warning(
                "SMTP is not configured, email not sent", to=to, subject=subject, body=body
            )
        return

    message = EmailMessage()
    message["From"] = settings.SMTP_FROM
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP(
            settings.SMTP_HOST, settings.SMTP_PORT, timeout=_SMTP_TIMEOUT_SECONDS
        ) as smtp:
            if settings.SMTP_USE_TLS:
                smtp.starttls()
            if settings.SMTP_USERNAME and settings.SMTP_PASSWORD:
                smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError):
        logger.exception("Failed to send email", subject=subject)


def send_password_reset_email(to: str, reset_url: str) -> None:
    body = (
        "Hello,\n\n"
        "We received a request to reset the password of your CerberDoc account.\n"
        "Open the link below to set a new password:\n\n"
        f"{reset_url}\n\n"
        f"The link is valid for {settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES} minutes "
        "and can be used once.\n\n"
        "If you did not ask for a password reset, you can ignore this message. "
        "Your password stays unchanged.\n"
    )
    send_email(to, "Reset your CerberDoc password", body)
