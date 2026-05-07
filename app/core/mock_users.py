import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.enums.analysis import UserRole
from app.models.user import User

_MOCK_FILE = Path(__file__).parent.parent.parent / "storage" / "mock_users.json"


# Słownik email -> (User, plain_password)
def load_mock_users() -> dict[str, tuple[User, str]]:
    raw = json.loads(_MOCK_FILE.read_text(encoding="utf-8"))
    users = {}
    for u in raw:
        user = User(
            id=uuid.UUID(u["id"]),
            email=u["email"],
            hashed_password="",  # nieużywane w mocku
            full_name=u["full_name"],
            role=UserRole(u["role"]),
            is_active=u["is_active"],
            is_verified=u["is_verified"],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        users[u["email"]] = (user, u["password"])
    return users


MOCK_USERS: dict[str, tuple[User, str]] = load_mock_users()