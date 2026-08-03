"""Створення користувача за запрошенням.

Публічної реєстрації немає навмисно (див. ADR-0004): це унеможливлює
абуз сховища і тримає обсяг чужих фінансових даних під контролем.

    python -m scripts.invite ivan@example.com --household "Родина Іванових"
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DB_URL = os.environ.get(
    "KOPIYKA_MIGRATION_DATABASE_URL",
    "postgresql+asyncpg://postgres:devpass@localhost:5432/kopiyka",
)


async def invite(email: str, household_name: str | None, join: str | None) -> int:
    engine = create_async_engine(DB_URL)
    async with engine.begin() as conn:
        existing = (
            await conn.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email})
        ).first()
        if existing:
            print(f"користувач {email} уже існує: {existing.id}")
            user_id = existing.id
        else:
            user_id = uuid.uuid4()
            await conn.execute(
                text("INSERT INTO users (id, email) VALUES (:id, :e)"),
                {"id": user_id, "e": email},
            )
            print(f"створено користувача {email}: {user_id}")

        if join:
            household_id = uuid.UUID(join)
            role = "member"
        else:
            household_id = uuid.uuid4()
            await conn.execute(
                text("INSERT INTO households (id, name) VALUES (:id, :n)"),
                {"id": household_id, "n": household_name or f"Бюджет {email.split('@')[0]}"},
            )
            role = "owner"

        await conn.execute(
            text(
                "INSERT INTO memberships (household_id, user_id, role) "
                "VALUES (:hh, :uid, :role) ON CONFLICT DO NOTHING"
            ),
            {"hh": household_id, "uid": user_id, "role": role},
        )
        print(f"household: {household_id} (роль {role})")
    await engine.dispose()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="invite")
    ap.add_argument("email")
    ap.add_argument("--household", help="назва нового household")
    ap.add_argument("--join", help="UUID існуючого household")
    args = ap.parse_args()
    return asyncio.run(invite(args.email, args.household, args.join))


if __name__ == "__main__":
    sys.exit(main())
