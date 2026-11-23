"""Conviction evidence from an already evaluated out-of-sample strategy run."""

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from app.core.config import Settings


def model_key(settings: Settings) -> str:
    config = {
        "ruleset": 1,
        "periods": settings.analytics_periods.model_dump(),
        "warmup": settings.analytics_warmup_bars,
        "opening_range": settings.analytics_opening_range_minutes,
        "rate_bps": settings.analytics_option_rate_bps,
        "dividend_bps": settings.analytics_option_dividend_bps,
        "snapshot_age": settings.analytics_option_snapshot_max_age_seconds,
        "scoring": settings.scoring.model_dump(
            exclude={
                "news_timeout_seconds",
                "news_max_age_hours",
                "news_max_items",
                "news_failure_cooldown_seconds",
                "min_calibration_observations",
            }
        ),
    }
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def conviction(result: Mapping[str, Any] | None, minimum: int) -> dict[str, object]:
    observations = int(result["observations"]) if result else 0
    if result is None or observations < minimum:
        return {
            "status": "uncalibrated",
            "win_probability_pct": None,
            "observations": observations,
            "minimum_observations": minimum,
        }
    return {
        "status": "calibrated",
        "win_probability_pct": 100 * int(result["wins"]) / observations,
        "observations": observations,
        "minimum_observations": minimum,
        "wins": int(result["wins"]),
        "evaluated_through": result["evaluated_through"].isoformat(),
        "available_at": result["available_at"].isoformat(),
    }
