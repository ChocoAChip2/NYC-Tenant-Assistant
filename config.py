"""Configuration helpers for environment variables used across the app."""

import os
from dataclasses import dataclass

# How many numbered key slots (GEMINI_API_KEY_2 ... _N) load_settings looks for.
MAX_NUMBERED_GEMINI_KEYS = 10


@dataclass(frozen=True)
class Settings:
    """Typed container for values loaded from the deployment environment."""

    supabase_url: str | None
    supabase_key: str | None
    gemini_api_keys: tuple[str, ...]
    flask_secret_key: str
    port: int


def load_gemini_api_keys() -> tuple[str, ...]:
    """Collect every configured Gemini key, in the order they should be tried.

    Accepts either a comma-separated GEMINI_API_KEY or numbered GEMINI_API_KEY_2
    through GEMINI_API_KEY_10 slots, so extra keys can be added in the Render
    dashboard without touching the code. Duplicates are dropped so a key pasted
    into two slots does not get retried twice for no reason.
    """

    keys: list[str] = []

    def add(raw: str | None) -> None:
        for part in (raw or "").split(","):
            key = part.strip()
            if key and key not in keys:
                keys.append(key)

    add(os.environ.get("GEMINI_API_KEY"))
    for index in range(2, MAX_NUMBERED_GEMINI_KEYS + 1):
        add(os.environ.get(f"GEMINI_API_KEY_{index}"))

    return tuple(keys)


def load_settings() -> Settings:
    """Read environment variables once and return them as a Settings object."""

    # app.py and supabase_service.py both rely on these values, so this function
    # keeps the environment-to-Python mapping in one place.
    return Settings(
        supabase_url=os.environ.get("SUPABASE_URL"),
        supabase_key=os.environ.get("SUPABASE_KEY"),
        gemini_api_keys=load_gemini_api_keys(),
        flask_secret_key=os.environ.get("FLASK_SECRET_KEY", "change-me-in-render"),
        port=int(os.environ.get("PORT", 5000)),
    )
