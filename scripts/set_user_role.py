"""Change the role of an existing user.

Roles cannot be chosen at registration, so this is how an account becomes an admin.

Usage:
    python scripts/set_user_role.py user@example.com admin
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import AsyncSessionLocal
from app.enums.analysis import UserRole
from app.repositories.user import UserRepository


async def set_user_role(email: str, role: UserRole) -> bool:
    async with AsyncSessionLocal() as session:
        user = await UserRepository(session).get_by_email(email)
        if user is None:
            return False

        user.role = role
        await session.commit()
        return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Change the role of an existing user.")
    parser.add_argument("email")
    parser.add_argument("role", choices=[role.value for role in UserRole])
    args = parser.parse_args()

    if not asyncio.run(set_user_role(args.email, UserRole(args.role))):
        print(f"User {args.email} not found", file=sys.stderr)
        return 1

    print(f"User {args.email} now has the role {args.role}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
