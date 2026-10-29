"""Wires the SOW generator into an application: its settings, storage and rate limiter."""

from fastapi import FastAPI

from app.core.config import SowSettings
from app.core.security import SlidingWindowRateLimiter
from app.modules.sow_generator.repositories.sow_storage import create_sow_storage


def install_sow_generator(app: FastAPI, settings: SowSettings) -> None:
    """Attach what the SOW controllers read from `app.state`."""
    app.state.sow_settings = settings
    app.state.sow_storage = create_sow_storage(settings)
    app.state.sow_rate_limiter = SlidingWindowRateLimiter(settings.sow_rate_limit_per_minute)
