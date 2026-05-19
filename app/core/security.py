from datetime import UTC, datetime, timedelta
from typing import Any, cast

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings


def hash_password(password: str) -> str:
    return cast(str, bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode())


def verify_password(plain: str, hashed: str) -> bool:
    return cast(bool, bcrypt.checkpw(plain.encode(), hashed.encode()))


def create_access_token(subject: str | int, extra_claims: dict[str, Any] | None = None) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    payload: dict[str, Any] = {"sub": str(subject), "exp": expire, "type": "access"}
    if extra_claims:
        payload.update(extra_claims)
    return str(jwt.encode(payload, settings.APP_SECRET_KEY, algorithm=settings.JWT_ALGORITHM))


def create_refresh_token(subject: str | int) -> str:
    expire = datetime.now(UTC) + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    payload: dict[str, Any] = {"sub": str(subject), "exp": expire, "type": "refresh"}
    return str(jwt.encode(payload, settings.APP_SECRET_KEY, algorithm=settings.JWT_ALGORITHM))


def decode_token(token: str) -> dict[str, Any]:
    try:
        decoded: dict[str, Any] = jwt.decode(
            token, settings.APP_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
    except JWTError as exc:
        raise ValueError("Invalid or expired token") from exc
    else:
        return decoded
