"""Configuration helpers for environment variables used across the app."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Typed container for values loaded from the deployment environment."""

    supabase_url: str | None
    supabase_key: str | None
    flask_secret_key: str
    port: int


def load_settings() -> Settings:
    """Read environment variables once and return them as a Settings object."""

    # app.py and supabase_service.py both rely on these values, so this function
    # keeps the environment-to-Python mapping in one place.
    return Settings(
        supabase_url=os.environ.get("SUPABASE_URL"),
        supabase_key=os.environ.get("SUPABASE_KEY"),
        flask_secret_key=os.environ.get("FLASK_SECRET_KEY", "change-me-in-render"),
        port=int(os.environ.get("PORT", 5000)),
    )
