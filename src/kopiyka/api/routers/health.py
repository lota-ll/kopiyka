"""Health та readiness.

``/healthz`` — процес живий (liveness, без БД).
``/readyz``  — застосунок готовий приймати трафік (перевіряє БД).
Різниця важлива: якщо змішати їх, Kubernetes перезапускатиме под щоразу,
коли моргне база, замість того щоб просто вивести його з балансування.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from kopiyka.api.schemas import HealthOut
from kopiyka.db.session import admin_session

router = APIRouter(tags=["system"])
VERSION = "0.1.0"


@router.get("/healthz", response_model=HealthOut)
async def healthz() -> HealthOut:
    return HealthOut(status="ok", version=VERSION, db="not-checked")


@router.get("/readyz", response_model=HealthOut)
async def readyz() -> HealthOut:
    try:
        async with admin_session() as session:
            await session.execute(text("SELECT 1"))
        db_state = "ok"
    except Exception:
        db_state = "unavailable"
    return HealthOut(status="ok" if db_state == "ok" else "degraded", version=VERSION, db=db_state)
