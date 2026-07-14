from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .cache import PersistentTTLCache
from .config import Settings, get_settings
from .errors import ApiError
from .models import RouteRequest
from .service import EcoService
from .upstream import UpstreamClient

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    logging.basicConfig(
        level=getattr(logging, app_settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # httpx logs full query strings at INFO, which would expose the Warsaw API token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        cache = PersistentTTLCache(app_settings.cache_dir, app_settings.cache_size_limit_bytes)
        timeout = httpx.Timeout(
            app_settings.request_timeout_seconds,
            connect=app_settings.connect_timeout_seconds,
        )
        limits = httpx.Limits(
            max_connections=app_settings.http_max_connections,
            max_keepalive_connections=app_settings.http_max_keepalive_connections,
        )
        async with httpx.AsyncClient(
            timeout=timeout,
            limits=limits,
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                "User-Agent": app_settings.project_user_agent,
            },
        ) as client:
            application.state.cache = cache
            application.state.eco = EcoService(app_settings, UpstreamClient(client), cache)
            yield
        await cache.close()

    application = FastAPI(
        title=app_settings.app_name,
        version=app_settings.app_version,
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    @application.exception_handler(ApiError)
    async def api_error_handler(_request: Request, error: ApiError) -> JSONResponse:
        return JSONResponse({"error": error.message}, status_code=error.status_code)

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, error: RequestValidationError
    ) -> JSONResponse:
        errors = error.errors()
        message = str(errors[0].get("ctx", {}).get("error") or errors[0]["msg"])
        message = message.removeprefix("Value error, ")
        return JSONResponse({"error": message}, status_code=400)

    @application.exception_handler(Exception)
    async def unexpected_error_handler(_request: Request, error: Exception) -> JSONResponse:
        logger.exception("Unexpected API error", exc_info=error)
        return JSONResponse({"error": "An unexpected server error occurred."}, status_code=500)

    @application.get("/api/health")
    async def health(request: Request) -> dict[str, Any]:
        return {
            "ok": True,
            "warsawTokenConfigured": bool(app_settings.warsaw_token),
            "cache": await request.app.state.cache.info(),
        }

    @application.get("/api/air")
    async def air(request: Request) -> dict[str, Any]:
        return await request.app.state.eco.get_air_quality()

    @application.post("/api/route")
    async def route(route_request: RouteRequest, request: Request) -> dict[str, Any]:
        return await request.app.state.eco.build_green_route(route_request)

    @application.get("/")
    async def root() -> dict[str, str]:
        return {"service": app_settings.app_name, "docs": "/docs"}

    return application


app = create_app()
