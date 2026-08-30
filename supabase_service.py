"""Supabase client wrapper used by the Flask routes.

This file centralizes all auth/data communication with Supabase so routes.py
can focus on request handling instead of client setup details.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

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

    def verify_user_jwt(self, access_token: str):
        """Validate an incoming Supabase JWT and return the authenticated user."""

        if not self.client:
            raise RuntimeError("Supabase is not configured yet.")
        return self.client.auth.get_user(access_token)

    def build_user_scoped_client(self, access_token: str) -> Client:
        """Create a request-scoped Supabase client that carries the user's JWT.

        Using the user's JWT (instead of a service-role key) ensures RLS policies
        are enforced for every select/insert/update call.
        """

        if not self.client:
            raise RuntimeError("Supabase is not configured yet.")

        auth_client = create_client(str(self.client.supabase_url), self.client.supabase_key)
        auth_client.postgrest.auth(access_token)
        return auth_client

    def create_conversation(self, user_client: Client, user_id: str, title: str) -> str:
        """Create a new conversation row for the authenticated user and return its id."""

        response = (
            user_client
            .table("conversations")
            .insert({"user_id": user_id, "title": title})
            .execute()
        )
        return response.data[0]["id"]

    def ensure_conversation_for_user(self, user_client: Client, conversation_id: str, user_id: str) -> dict:
        """Ensure the conversation exists and belongs to the user, and return it.

        The row is returned rather than discarded so callers can check the
        current title without spending a second round trip on it.
        """

        response = (
            user_client
            .table("conversations")
            .select("id,title")
            .eq("id", conversation_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )

        if not response.data:
            raise ValueError("Conversation not found for this user.")

        return response.data[0]

    def fetch_messages_for_conversation(self, user_client: Client, conversation_id: str) -> list[dict]:
        """Fetch full message history for a conversation, ordered oldest->newest."""

        response = (
            user_client
            .table("messages")
            .select("role,content,created_at")
            .eq("conversation_id", conversation_id)
            .order("created_at", desc=False)
            .execute()
        )
        return response.data or []

    def insert_message(self, user_client: Client, message: dict) -> None:
        """Insert a single message row under RLS."""

        user_client.table("messages").insert(message).execute()

    def list_conversations(self, user_client: Client) -> list[dict]:
        """Return user conversations sorted by most recent activity."""

        response = (
            user_client
            .table("conversations")
            .select("id,title,created_at,updated_at")
            .order("updated_at", desc=True)
            .execute()
        )
        return response.data or []

    def update_conversation_title(self, user_client: Client, conversation_id: str, title: str) -> None:
        """Rename a conversation. RLS keeps this scoped to the owner."""

        (
            user_client
            .table("conversations")
            .update({"title": title, "updated_at": datetime.now(timezone.utc).isoformat()})
            .eq("id", conversation_id)
            .execute()
        )

    def touch_conversation(self, user_client: Client, conversation_id: str) -> None:
        """Bump updated_at so list_conversations sorts by real activity.

        Inserting a message does not touch the parent row on its own, so without
        this the sidebar stays frozen in creation order.
        """

        (
            user_client
            .table("conversations")
            .update({"updated_at": datetime.now(timezone.utc).isoformat()})
            .eq("id", conversation_id)
            .execute()
        )
