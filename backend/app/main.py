
"""Основное приложение ЦУТ Improvement Auto."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel

from app.api.v1.router import api_router
from app.core.config import get_settings

from app.services.max_bot_service import (
    router as max_link_router,
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


# === CUT FRONTEND STATIC SERVING: BEGIN ===
# The mount is deliberately LAST. API, MAX routes and /health always win.
# Vite dev mode at :5173 is unaffected.
from os import environ as _cut_environ
from pathlib import Path as _CutPath
from fastapi.staticfiles import StaticFiles as _CutStaticFiles

from app.services.frontend_release import resolve_frontend_dist as _cut_resolve_frontend_dist

_cut_frontend_override = _cut_environ.get("CUT_FRONTEND_DIST", "").strip()
_cut_frontend_dist = _cut_resolve_frontend_dist(
    _CutPath(__file__).resolve().parents[2],
    _cut_frontend_override,
)
if _cut_frontend_dist.is_dir() and (_cut_frontend_dist / "index.html").is_file():
    app.mount(
        "/",
        _CutStaticFiles(directory=str(_cut_frontend_dist), html=True),
        name="cut_frontend",
    )
# === CUT FRONTEND STATIC SERVING: END ===
