import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings


def hash_password(password: str) -> str:
    return cast(str, bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode())


def verify_password(plain: str, hashed: str) -> bool:
    return cast(bool, bcrypt.checkpw(plain.encode(), hashed.encode()))


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# Name of the token claim that ties a session to the password it was created with
PASSWORD_VERSION_CLAIM = "pwd"  # noqa: S105


def password_version(password_changed_at: datetime | None) -> int:
    """Number identifying the current password of a user: 0 until the password is changed."""
    if password_changed_at is None:
        return 0
    return (password_changed_at - _EPOCH) // timedelta(microseconds=1)


def is_token_for_current_password(
    payload: dict[str, Any], password_changed_at: datetime | None
) -> bool:
    """False for tokens issued before the last password change, so old sessions stop working."""
    return bool(payload.get(PASSWORD_VERSION_CLAIM, 0) == password_version(password_changed_at))


def create_access_token(subject: str | int, extra_claims: dict[str, Any] | None = None) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    payload: dict[str, Any] = {"sub": str(subject), "exp": expire, "type": "access"}
    if extra_claims:
        payload.update(extra_claims)
    return str(jwt.encode(payload, settings.APP_SECRET_KEY, algorithm=settings.JWT_ALGORITHM))


def create_refresh_token(subject: str | int, extra_claims: dict[str, Any] | None = None) -> str:
    expire = datetime.now(UTC) + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    payload: dict[str, Any] = {"sub": str(subject), "exp": expire, "type": "refresh"}
    if extra_claims:
        payload.update(extra_claims)
    return str(jwt.encode(payload, settings.APP_SECRET_KEY, algorithm=settings.JWT_ALGORITHM))


def generate_password_reset_token() -> str:
    return secrets.token_urlsafe(32)


def hash_password_reset_token(token: str) -> str:
    """Only the hash is stored, so a leaked database does not give working reset links."""
    return hashlib.sha256(token.encode()).hexdigest()


def decode_token(token: str) -> dict[str, Any]:
    try:
        decoded: dict[str, Any] = jwt.decode(
            token, settings.APP_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
    except JWTError as exc:
        raise ValueError("Invalid or expired token") from exc
    else:
        return decoded
