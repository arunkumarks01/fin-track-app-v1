"""Scripted Kite HTTP server for `httpx.MockTransport` — no network in any test."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

Handler = Callable[[httpx.Request], httpx.Response]

KITE_KEY = "kitekey123"
KITE_SECRET = "kitesecret789"


def kite_ok(data: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json={"status": "success", "data": data})


def kite_error(status: int, error_type: str, message: str = "error") -> httpx.Response:
    return httpx.Response(
        status, json={"status": "error", "error_type": error_type, "message": message}
    )


@dataclass
class FakeKiteHTTP:
    """Route table keyed by (METHOD, path); each entry is a list of responses served in order
    (the last one repeats). Every request is recorded."""

    routes: dict[tuple[str, str], list[httpx.Response | Handler]] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)

    def on(self, method: str, path: str, *responses: httpx.Response | Handler) -> None:
        self.routes[(method.upper(), path)] = list(responses)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queue = self.routes.get((request.method, request.url.path))
        if not queue:
            return httpx.Response(404, json={"status": "error", "error_type": "NotFound"})
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        return item(request) if callable(item) else item

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def form(self, index: int = -1) -> dict[str, str]:
        from urllib.parse import parse_qsl

        return dict(parse_qsl(self.requests[index].content.decode()))

    @staticmethod
    def body(response: httpx.Response) -> Any:
        return json.loads(response.content)
