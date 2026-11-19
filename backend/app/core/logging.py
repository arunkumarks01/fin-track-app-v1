"""Logging with secret masking.

Masking is applied to the *fully formatted* record (message, args and traceback), so a
secret can't leak through `%s` args or an exception string. Two layers:
1. exact known secrets (registered from Settings at boot), and
2. shape-based patterns (DSN passwords, bearer tokens, key=value credentials).
"""

from __future__ import annotations

import logging
import re
import sys
import threading
from collections.abc import Iterable

MASK = "***"
_MIN_SECRET_LEN = 6  # shorter values would mask ordinary words

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # postgresql://user:password@host  -> postgresql://user:***@host
    (re.compile(r"(?P<pre>\b[a-z][a-z0-9+.-]*://[^:/\s@]+:)[^@\s]+(?=@)", re.I), rf"\g<pre>{MASK}"),
    # Authorization: Bearer <token>
    (re.compile(r"(?P<pre>\bbearer\s+)[A-Za-z0-9._~+/=-]+", re.I), rf"\g<pre>{MASK}"),
    # api_key=..., "access_token": "...", password: ...
    (
        re.compile(
            r"(?P<pre>\b(?:api[_-]?key|api[_-]?secret|access[_-]?token|request[_-]?token|"
            r"auth[_-]?token|secret|password|passwd|token)\b[\"']?\s*[:=]\s*[\"']?)"
            r"[^\s\"',&}]+",
            re.I,
        ),
        rf"\g<pre>{MASK}",
    ),
)


class SecretRegistry:
    """Thread-safe set of exact secret strings to scrub from logs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._secrets: frozenset[str] = frozenset()

    def register(self, values: Iterable[str]) -> None:
        fresh = {v for v in values if v and len(v) >= _MIN_SECRET_LEN}
        with self._lock:
            self._secrets = self._secrets | fresh

    def clear(self) -> None:
        with self._lock:
            self._secrets = frozenset()

    def snapshot(self) -> frozenset[str]:
        return self._secrets


SECRETS = SecretRegistry()


def mask(text: str, secrets: Iterable[str] | None = None) -> str:
    known = SECRETS.snapshot() if secrets is None else frozenset(secrets)
    # longest first so a secret containing another secret is fully masked
    for secret in sorted(known, key=len, reverse=True):
        text = text.replace(secret, MASK)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class MaskingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return mask(super().format(record))


LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(level: str, secrets: Iterable[str] = ()) -> None:
    """Route all logging (incl. uvicorn/apscheduler) through one masking handler."""
    SECRETS.register(secrets)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(MaskingFormatter(LOG_FORMAT))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "apscheduler", "asyncpg"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
