"""Gemini client wrapper used by chat routes."""

from dataclasses import dataclass
from google import genai

from config import Settings


@dataclass
class AIService:
    client: genai.Client | None
    initialization_error: str | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> "AIService":
        if not settings.gemini_api_key:
            return cls(client=None, initialization_error="Gemini API key is missing.")

        try:
            client = genai.Client(api_key=settings.gemini_api_key)
            return cls(client=client)
        except Exception as exc:
            return cls(client=None, initialization_error=f"Failed to create Gemini client: {exc}")

    def is_ready(self) -> bool:
        return self.client is not None

    def generate_reply(self, messages: list[dict[str, str]]) -> str:
        if not self.client:
            raise RuntimeError("Gemini is not configured yet.")

        contents = []
        system_instructions = []

        for message in messages:
            role = message.get("role", "").strip().lower()
            content = message.get("content", "").strip()

            if not content or role not in {"system", "user", "assistant"}:
                continue

            if role == "system":
                system_instructions.append(content)
                continue

            contents.append({
                "role": "model" if role == "assistant" else "user",
                "parts": [{"text": content}],
            })

        config = None
        if system_instructions:
            config = {"system_instruction": "\n".join(system_instructions)}

        response = self.client.models.generate_content(
            model="gemini-1.5-flash-latest",
            contents=contents,
            config=config,
        )

        return (response.text or "").strip() or "I could not generate a response."

