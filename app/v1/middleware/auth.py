import secrets
from typing import Iterable, Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.v1.core.settings import API_BEARER_ROLE, API_BEARER_SUBJECT, API_BEARER_TOKEN


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, allowlist: Optional[Iterable[str]] = None):
        super().__init__(app)
        self.allowlist = set(allowlist or [])

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        is_allowlisted = any(path.startswith(route) for route in self.allowlist)
        auth_header = request.headers.get("authorization")

        if is_allowlisted:
            return await call_next(request)

        if not auth_header:
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing Authorization header"},
            )

        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid Authorization header"},
            )

        if not secrets.compare_digest(token, API_BEARER_TOKEN):
            return JSONResponse(
                status_code=401,
                content={"detail": "Could not validate credentials"},
            )

        request.state.user = {
            "sub": API_BEARER_SUBJECT,
            "role": API_BEARER_ROLE,
        }
        return await call_next(request)
