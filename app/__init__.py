import os
import asyncio
import contextlib
import logging

from beanie import init_beanie
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from app.db.mongo import get_client
from app.v1.core.settings import (
    APP_NAME,
    APP_VERSION,
    CORS_ALLOW_ORIGINS,
    MONGO_DB,
)
from app.v1.middleware.auth import AuthMiddleware
from app.v1.routes import api_router
from app.v1.schemas.jobqueuemodel import JobQueueDocument


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    force=True,
)


def create_app() -> FastAPI:
    app = FastAPI(title=APP_NAME, version=APP_VERSION)
    mongo_client = get_client()
    app.state.mongo_client = mongo_client
    app.state.queue_poller_task = None
    app.state.log_archive_task = None
    allow_origins = (
        ["*"]
        if CORS_ALLOW_ORIGINS.strip() == "*"
        else [origin.strip() for origin in CORS_ALLOW_ORIGINS.split(",") if origin.strip()]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(
        AuthMiddleware,
        allowlist=["/", "/health", "/docs", "/openapi.json"],
    )
    app.include_router(api_router, prefix="/api/v1")

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=APP_NAME,
            version=APP_VERSION,
            routes=app.routes,
        )
        schema.setdefault("components", {})["securitySchemes"] = {
            "BearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "Token",
            }
        }
        schema["security"] = [{"BearerAuth": []}]
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "status": 422,
                "message": "Validation error",
                "data": [
                    {key: str(value) if key == "ctx" else value for key, value in err.items()}
                    for err in exc.errors()
                ],
            },
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "status": exc.status_code,
                "message": exc.detail,
                "data": None,
            },
        )

    @app.get("/", tags=["info"], include_in_schema=False)
    async def root():
        return {
            "status": "online",
            "service": APP_NAME,
            "version": APP_VERSION,
            "docs": "/docs",
            "build_sha": os.getenv("APP_BUILD_SHA", "unknown"),
        }

    @app.get("/health", tags=["info"], include_in_schema=False)
    async def health():
        return {
            "status": "healthy",
            "build_sha": os.getenv("APP_BUILD_SHA", "unknown"),
        }

    @app.on_event("startup")
    async def startup_event() -> None:
        from app.v1.modules.bot.services.queue_service import (
            get_queue_poll_sleep_seconds,
            recover_incomplete_jobs,
            schedule_queue_poll_if_idle,
        )
        from app.v1.modules.bot.services.log_archive_service import (
            archive_previous_day_logs,
            run_daily_log_archive_forever,
        )

        await init_beanie(
            database=mongo_client[MONGO_DB],
            document_models=[JobQueueDocument],
        )
        await recover_incomplete_jobs()
        try:
            await asyncio.to_thread(archive_previous_day_logs)
        except Exception:
            logging.getLogger(__name__).exception("Initial bot log archive run failed")

        async def _queue_poller() -> None:
            while True:
                schedule_queue_poll_if_idle()
                await asyncio.sleep(await get_queue_poll_sleep_seconds())

        app.state.queue_poller_task = asyncio.create_task(_queue_poller())
        app.state.log_archive_task = asyncio.create_task(run_daily_log_archive_forever())

    @app.on_event("shutdown")
    async def shutdown_event() -> None:
        queue_poller_task = getattr(app.state, "queue_poller_task", None)
        if queue_poller_task is not None:
            queue_poller_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await queue_poller_task
        log_archive_task = getattr(app.state, "log_archive_task", None)
        if log_archive_task is not None:
            log_archive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await log_archive_task
        mongo_client.close()

    return app


app = create_app()
