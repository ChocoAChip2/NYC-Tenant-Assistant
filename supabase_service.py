"""Supabase client wrapper used by the Flask routes.

This file centralizes all auth-related communication with Supabase so routes.py
can focus on request handling instead of client setup details.
"""

from dataclasses import dataclass

from supabase import Client, create_client

from config import Settings


@dataclass
class SupabaseService:
    """Small service layer that owns the Supabase client and auth actions."""

    client: Client | None
    initialization_error: str | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> "SupabaseService":
        """Create the shared Supabase client from values loaded in config.py."""

        # Return a readable startup error when app.py has not been given the
        # environment variables needed to talk to Supabase.
        if not settings.supabase_url or not settings.supabase_key:
            return cls(client=None, initialization_error="Supabase keys are missing.")

        try:
            # Build the SDK client once and reuse it for every request.
            client = create_client(settings.supabase_url, settings.supabase_key)
            return cls(client=client)
        except Exception as exc:
            return cls(client=None, initialization_error=f"Failed to create Supabase client: {exc}")

    def is_ready(self) -> bool:
        """Tell app.py whether auth routes can safely use the Supabase client."""

        return self.client is not None

    def sign_up(self, email: str, password: str) -> None:
        """Create a new Supabase account for the signup route."""

        if not self.client:
            raise RuntimeError("Supabase is not configured yet.")
        self.client.auth.sign_up({"email": email, "password": password})

    def sign_in(self, email: str, password: str):
        """Authenticate an existing Supabase user for the login route."""

        if not self.client:
            raise RuntimeError("Supabase is not configured yet.")
        return self.client.auth.sign_in_with_password({"email": email, "password": password})
