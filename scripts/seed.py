"""Наповнення довідника категорій та створення демо-household.

Запуск: ``python -m scripts.seed`` (або ``make seed``).
Ідемпотентний — повторний запуск нічого не дублює.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

SEEDS = Path(os.environ.get("KOPIYKA_SEEDS_DIR", Path(__file__).resolve().parent.parent / "seeds"))

# Seed працює від імені власника: глобальні категорії не належать
# жодному household, тому RLS-контексту для них не існує.
DB_URL = os.environ.get(
    "KOPIYKA_MIGRATION_DATABASE_URL",
    "postgresql+asyncpg://postgres:devpass@localhost:5432/kopiyka",
)


async def main() -> int:
    categories = yaml.safe_load((SEEDS / "categories.yaml").read_text("utf-8"))
    engine = create_async_engine(DB_URL)

    async with engine.begin() as conn:
        for category in categories:
            await conn.execute(
                text(
                    "INSERT INTO categories (id, household_id, slug, name, kind) "
                    "VALUES (:id, NULL, :slug, :name, :kind) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "id": uuid.uuid4(),
                    "slug": category["slug"],
                    "name": category["name"],
                    "kind": category.get("kind", "expense"),
                },
            )

        if os.environ.get("KOPIYKA_ENV", "local") == "local":
            user_id, household_id = uuid.uuid4(), uuid.uuid4()
            row = await conn.execute(
                text(
                    "INSERT INTO users (id, email, display_name) "
                    "VALUES (:id, :email, :name) ON CONFLICT (email) DO NOTHING "
                    "RETURNING id"
                ),
                {"id": user_id, "email": "dev@example.com", "name": "Dev"},
            )
            created = row.first()
            if created:
                await conn.execute(
                    text("INSERT INTO households (id, name) VALUES (:id, :name)"),
                    {"id": household_id, "name": "Демо-бюджет"},
                )
                await conn.execute(
                    text(
                        "INSERT INTO memberships (household_id, user_id, role) "
                        "VALUES (:hh, :uid, 'owner')"
                    ),
                    {"hh": household_id, "uid": created.id},
                )
                await conn.execute(
                    text(
                        "INSERT INTO identities (id, user_id, provider, subject) "
                        "VALUES (:id, :uid, 'dev', 'dev@example.com')"
                    ),
                    {"id": uuid.uuid4(), "uid": created.id},
                )
                print(f"демо-household: {household_id}")
                print("виклик API: -H 'X-Dev-User: dev@example.com'")

    await engine.dispose()
    print(f"категорій оброблено: {len(categories)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
