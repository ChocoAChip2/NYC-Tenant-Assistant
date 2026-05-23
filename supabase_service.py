from dataclasses import dataclass

from supabase import Client, create_client

from config import Settings


@dataclass
class SupabaseService:
    client: Client | None
    initialization_error: str | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> "SupabaseService":
        if not settings.supabase_url or not settings.supabase_key:
            return cls(client=None, initialization_error="Supabase keys are missing.")

        try:
            client = create_client(settings.supabase_url, settings.supabase_key)
            return cls(client=client)
        except Exception as exc:
            return cls(client=None, initialization_error=f"Failed to create Supabase client: {exc}")

    def is_ready(self) -> bool:
        return self.client is not None

    def sign_up(self, email: str, password: str) -> None:
        if not self.client:
            raise RuntimeError("Supabase is not configured yet.")
        self.client.auth.sign_up({"email": email, "password": password})

    def sign_in(self, email: str, password: str):
        if not self.client:
            raise RuntimeError("Supabase is not configured yet.")
        return self.client.auth.sign_in_with_password({"email": email, "password": password})
