"""Gemini client wrapper used by chat routes."""

from dataclasses import dataclass

from google import genai

from config import Settings


@dataclass
class AIService:
    """Small service layer that owns the Gemini client and chat generation."""

    client: genai.Client | None
    initialization_error: str | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> "AIService":
        """Create the shared Gemini client from values loaded in config.py."""

        if not settings.gemini_api_key:
            return cls(client=None, initialization_error="Gemini API key is missing.")

        try:
            client = genai.Client(api_key=settings.gemini_api_key)
            return cls(client=client)
        except Exception as exc:
            return cls(client=None, initialization_error=f"Failed to create Gemini client: {exc}")

    def is_ready(self) -> bool:
        """Tell app.py whether chat routes can safely use the Gemini client."""

        return self.client is not None

    def generate_reply(self, messages: list[dict[str, str]]) -> str:
        """Generate an assistant reply from the current chat history."""

        if not self.client:
            raise RuntimeError("Gemini is not configured yet.")

        conversation = []
        for message in messages:
            role = message.get("role", "").strip().lower()
            content = message.get("content", "").strip()
            if not content or role not in {"system", "user", "assistant"}:
                continue
            conversation.append(f"{role.upper()}: {content}")

        if not conversation:
            raise ValueError("No valid messages were provided.")

        response = self.client.models.generate_content(
            model="gemini-1.5-flash",
            contents="\n".join(conversation),
        )
        return (response.text or "").strip() or "I could not generate a response."
