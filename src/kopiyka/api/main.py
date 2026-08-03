"""Точка входу FastAPI."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from kopiyka.api.deps import CurrentPrincipal
from kopiyka.api.routers import accounts, health, imports
from kopiyka.api.schemas import MeOut
from kopiyka.config import get_settings

settings = get_settings()
logging.basicConfig(level=settings.log_level)

app = FastAPI(
    title="Kopiyka",
    version="0.1.0",
    description="Аналітика витрат на основі банківських виписок",
    docs_url="/docs" if settings.env != "prod" else None,
)

if settings.env == "local":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(health.router)
app.include_router(accounts.router)
app.include_router(imports.router)


@app.get("/api/v1/me", response_model=MeOut, tags=["auth"])
async def me(principal: CurrentPrincipal) -> MeOut:
    return MeOut(
        user_id=principal.user_id,
        email=principal.email,
        household_id=principal.household_id,
        role=principal.role,
    )
