"""asyncpg pool construction (raw SQL, no ORM)."""

from __future__ import annotations

from typing import Any

import asyncpg

from app.core.config import Settings

APPLICATION_NAME = "fintrack-v2"


async def create_pool(settings: Settings) -> Any:
    return await asyncpg.create_pool(
        dsn=settings.database_url.get_secret_value(),
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        timeout=settings.db_connect_timeout_seconds,
        server_settings={
            "application_name": APPLICATION_NAME,
            "search_path": "app,public",
            "timezone": "UTC",
        },
    )
