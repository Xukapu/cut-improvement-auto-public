
"""Основное приложение ЦУТ Improvement Auto."""

from contextlib import asynccontextmanager
from functools import lru_cache
from os import environ
from pathlib import Path

from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.db.session import engine
from app.services.frontend_release import resolve_frontend_dist
from app.services.max_bot_service import (
    router as max_link_router,
)
from app.services.max_bot_service import (
    start_max_bot,
    stop_max_bot,
)
from app.services.max_link_screen import (
    router as max_screen_router,
)
from app.services.notification_scheduler import (
    start_notification_scheduler,
    stop_notification_scheduler,
)

settings = get_settings()


class HealthResponse(BaseModel):
    status: str
    application: str
    version: str


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await start_notification_scheduler()
    await start_max_bot()

    try:
        yield
    finally:
        await stop_max_bot()
        await stop_notification_scheduler()


app = FastAPI(
    lifespan=lifespan,
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url=None,
    openapi_url=(
        "/openapi.json"
        if settings.docs_enabled
        else None
    ),
)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=settings.allowed_hosts,
)

app.include_router(api_router)

app.include_router(
    max_link_router,
    prefix="/api/v1",
)

app.include_router(
    max_screen_router,
    prefix="/api/v1",
)


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["system"],
    summary="Проверка состояния приложения",
)
def healthcheck() -> HealthResponse:
    return HealthResponse(
        status="ok",
        application=settings.app_name,
        version=settings.app_version,
    )


@lru_cache(maxsize=1)
def expected_database_revisions() -> frozenset[str]:
    migrations = Path(__file__).resolve().parent / "db" / "migrations"
    return frozenset(ScriptDirectory(str(migrations)).get_heads())


@app.get(
    "/ready",
    response_model=HealthResponse,
    tags=["system"],
    summary="Готовность приложения и базы данных",
)
def readiness_check() -> HealthResponse:
    try:
        expected = expected_database_revisions()
        with engine.connect() as connection:
            current = frozenset(
                connection.scalars(text("SELECT version_num FROM alembic_version"))
            )
    except (CommandError, OSError, SQLAlchemyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="База данных недоступна или не подготовлена.",
        ) from exc

    if not expected or current != expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Версия схемы базы данных отличается от версии приложения.",
        )

    return healthcheck()


# === CUT FRONTEND STATIC SERVING: BEGIN ===
# Register API and system routes before mounting the frontend.
# Vite dev mode at :5173 is unaffected.
frontend_dist = resolve_frontend_dist(
    Path(__file__).resolve().parents[2],
    environ.get("CUT_FRONTEND_DIST", "").strip(),
)
if frontend_dist.is_dir() and (frontend_dist / "index.html").is_file():
    app.mount(
        "/",
        StaticFiles(directory=str(frontend_dist), html=True),
        name="cut_frontend",
    )
# === CUT FRONTEND STATIC SERVING: END ===
