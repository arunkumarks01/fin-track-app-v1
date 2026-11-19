"""`uv run python -m app` — single process, no reload (D23)."""

from __future__ import annotations

import uvicorn

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.main import create_app


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level, settings.secret_values())
    uvicorn.run(
        create_app(settings),
        host=settings.api_host,
        port=settings.api_port,
        workers=1,
        log_config=None,  # keep the masking root handler
    )


if __name__ == "__main__":
    main()
