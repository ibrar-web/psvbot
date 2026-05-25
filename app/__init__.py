import gc
import os
import asyncio
import contextlib
import logging
import sys

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from app.db.mongo import get_client
from app.v1.core.settings import (
    APP_NAME,
    APP_VERSION,
    CORS_ALLOW_ORIGINS,
)
from app.v1.middleware.auth import AuthMiddleware
from app.v1.routes import api_router


LOG_MEMORY_CLEAR_INTERVAL_SECONDS = 3600  # 1 hour


def _clear_log_memory() -> None:
    """Periodic cleanup to prevent logging-related memory leaks.

    - Flushes all handlers
    - Removes duplicate StreamHandlers that may accumulate
    - Prunes stale file-based handlers from the bot logger
    - Runs gc.collect() to reclaim freed objects
    """
    bot_logger = logging.getLogger("app.v1.modules.bot")
    root_logger = logging.getLogger()

    # 1. Flush all handlers on root and bot loggers
    for logger_instance in (root_logger, bot_logger):
        for handler in list(logger_instance.handlers):
            try:
                handler.flush()
            except Exception:
                pass

    # 2. On bot logger: remove duplicate terminal handlers (keep only one)
    seen_terminal = False
    for handler in list(bot_logger.handlers):
        is_terminal = (
            isinstance(handler, logging.StreamHandler)
            and getattr(handler, "stream", None) in (sys.stderr, sys.stdout)
        )
        if is_terminal:
            if seen_terminal:
                bot_logger.removeHandler(handler)
                try:
                    handler.close()
                except Exception:
                    pass
            else:
                seen_terminal = True
        elif not isinstance(handler, logging.StreamHandler):
            # Remove any non-stream (file) handlers that shouldn't be there
            bot_logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

    # 3. On root logger: remove duplicate StreamHandlers
    terminal_handlers = [
        h for h in root_logger.handlers
        if isinstance(h, logging.StreamHandler)
        and getattr(h, "stream", None) in (sys.stderr, sys.stdout)
    ]
    for handler in terminal_handlers[1:]:
        root_logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    # 4. Force garbage collection
    gc.collect()

    logging.getLogger(__name__).info("Log memory cleared (hourly cleanup + gc)")


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
    app.state.log_memory_clear_task = None
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
        allowlist=[
            "/",
            "/health",
            "/docs",
            "/openapi.json",
            "/enqueue-task",
            "/execute-task",
            "/api/v1/bot/enqueue-task",
            "/api/v1/bot/execute-task",
            "/api/v1/bot/execute-test-task",
        ],
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

    @app.post("/enqueue-task", tags=["bot"], include_in_schema=True)
    async def enqueue_task(request: Request):
        from app.v1.modules.bot.services.queue_service import enqueue_task_payload

        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=400,
                detail="Task payload must be a JSON object",
            )
        return await enqueue_task_payload(payload)

    @app.post("/execute-task", tags=["bot"], include_in_schema=True)
    async def execute_task(request: Request):
        from app.v1.modules.bot.services.queue_service import enqueue_task_payload

        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=400,
                detail="Task payload must be a JSON object",
            )
        return await enqueue_task_payload(payload)

    @app.on_event("startup")
    async def startup_event() -> None:
        from app.v1.modules.bot.services.queue_service import (
            start_queue_workers,
        )

        await start_queue_workers(mongo_client)

        async def _log_memory_clearer() -> None:
            """Periodically flush logging handlers to free memory every hour."""
            while True:
                await asyncio.sleep(LOG_MEMORY_CLEAR_INTERVAL_SECONDS)
                try:
                    _clear_log_memory()
                except Exception:
                    logging.getLogger(__name__).exception("Hourly log memory clear failed")

        app.state.log_memory_clear_task = asyncio.create_task(_log_memory_clearer())

    @app.on_event("shutdown")
    async def shutdown_event() -> None:
        from app.v1.modules.bot.services.queue_service import stop_queue_workers

        await stop_queue_workers()
        log_memory_clear_task = getattr(app.state, "log_memory_clear_task", None)
        if log_memory_clear_task is not None:
            log_memory_clear_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await log_memory_clear_task
        mongo_client.close()

    return app


app = create_app()

import gc
import os
import asyncio
import contextlib
import logging
import sys

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from app.db.mongo import get_client
from app.v1.core.settings import (
    APP_NAME,
    APP_VERSION,
    CORS_ALLOW_ORIGINS,
)
from app.v1.middleware.auth import AuthMiddleware
from app.v1.routes import api_router


LOG_MEMORY_CLEAR_INTERVAL_SECONDS = 3600  # 1 hour


def _clear_log_memory() -> None:
    """Periodic cleanup to prevent logging-related memory leaks.

    - Flushes all handlers
    - Removes duplicate StreamHandlers that may accumulate
    - Prunes stale file-based handlers from the bot logger
    - Runs gc.collect() to reclaim freed objects
    """
    bot_logger = logging.getLogger("app.v1.modules.bot")
    root_logger = logging.getLogger()

    # 1. Flush all handlers on root and bot loggers
    for logger_instance in (root_logger, bot_logger):
        for handler in list(logger_instance.handlers):
            try:
                handler.flush()
            except Exception:
                pass

    # 2. On bot logger: remove duplicate terminal handlers (keep only one)
    seen_terminal = False
    for handler in list(bot_logger.handlers):
        is_terminal = (
            isinstance(handler, logging.StreamHandler)
            and getattr(handler, "stream", None) in (sys.stderr, sys.stdout)
        )
        if is_terminal:
            if seen_terminal:
                bot_logger.removeHandler(handler)
                try:
                    handler.close()
                except Exception:
                    pass
            else:
                seen_terminal = True
        elif not isinstance(handler, logging.StreamHandler):
            # Remove any non-stream (file) handlers that shouldn't be there
            bot_logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

    # 3. On root logger: remove duplicate StreamHandlers
    terminal_handlers = [
        h for h in root_logger.handlers
        if isinstance(h, logging.StreamHandler)
        and getattr(h, "stream", None) in (sys.stderr, sys.stdout)
    ]
    for handler in terminal_handlers[1:]:
        root_logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    # 4. Force garbage collection
    gc.collect()

    logging.getLogger(__name__).info("Log memory cleared (hourly cleanup + gc)")


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
    app.state.log_memory_clear_task = None
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
        allowlist=[
            "/",
            "/health",
            "/docs",
            "/openapi.json",
            "/enqueue-task",
            "/execute-task",
            "/api/v1/bot/enqueue-task",
            "/api/v1/bot/execute-task",
            "/api/v1/bot/execute-test-task",
        ],
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

    @app.post("/enqueue-task", tags=["bot"], include_in_schema=True)
    async def enqueue_task(request: Request):
        from app.v1.modules.bot.services.queue_service import enqueue_task_payload

        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=400,
                detail="Task payload must be a JSON object",
            )
        return await enqueue_task_payload(payload)

    @app.post("/execute-task", tags=["bot"], include_in_schema=True)
    async def execute_task(request: Request):
        from app.v1.modules.bot.services.queue_service import enqueue_task_payload

        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=400,
                detail="Task payload must be a JSON object",
            )
        return await enqueue_task_payload(payload)

    @app.on_event("startup")
    async def startup_event() -> None:
        from app.v1.modules.bot.services.queue_service import (
            start_queue_workers,
        )

        await start_queue_workers(mongo_client)

        async def _log_memory_clearer() -> None:
            """Periodically flush logging handlers to free memory every hour."""
            while True:
                await asyncio.sleep(LOG_MEMORY_CLEAR_INTERVAL_SECONDS)
                try:
                    _clear_log_memory()
                except Exception:
                    logging.getLogger(__name__).exception("Hourly log memory clear failed")

        app.state.log_memory_clear_task = asyncio.create_task(_log_memory_clearer())

    @app.on_event("shutdown")
    async def shutdown_event() -> None:
        from app.v1.modules.bot.services.queue_service import stop_queue_workers

        await stop_queue_workers()
        log_memory_clear_task = getattr(app.state, "log_memory_clear_task", None)
        if log_memory_clear_task is not None:
            log_memory_clear_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await log_memory_clear_task
        mongo_client.close()

    return app


app = create_app()
