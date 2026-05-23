import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    supabase_url: str | None
    supabase_key: str | None
    flask_secret_key: str
    port: int


def load_settings() -> Settings:
    return Settings(
        supabase_url=os.environ.get("SUPABASE_URL"),
        supabase_key=os.environ.get("SUPABASE_KEY"),
        flask_secret_key=os.environ.get("FLASK_SECRET_KEY", "change-me-in-render"),
        port=int(os.environ.get("PORT", 5000)),
    )
