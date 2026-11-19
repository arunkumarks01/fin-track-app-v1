"""Bearer-token gate on every mutating request (ARCHITECTURE §7).

A middleware rather than a per-route dependency, so it is fail-closed by construction:
any POST/PUT/PATCH/DELETE route added later is protected without anyone remembering to
opt in. Exemptions (e.g. the Telegram webhook, which has its own secret) must be added
explicitly — none exist in Phase 0.
"""

from __future__ import annotations

import secrets

from pydantic import SecretStr
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class BearerAuthMiddleware:
    def __init__(self, app: ASGIApp, token: SecretStr) -> None:
        self.app = app
        self._token = token.get_secret_value().encode("utf-8")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] in MUTATING_METHODS
            and not self._authorized(Headers(scope=scope).get("authorization", ""))
        ):
            response = JSONResponse(
                {"detail": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _authorized(self, header: str) -> bool:
        scheme, _, credential = header.partition(" ")
        if scheme.lower() != "bearer" or not credential:
            return False
        return secrets.compare_digest(credential.strip().encode("utf-8"), self._token)
