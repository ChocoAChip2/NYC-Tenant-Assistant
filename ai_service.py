"""Gemini client wrapper used by chat routes.

Chat replies must survive a single model being retired or a single API key
running out of quota, so every call walks a chain of (model, key) pairs instead
of trusting one combination. All of that recovery is deliberately invisible to
the user: routes.py either gets text back or gets one error.
"""

import logging
import re
import time
from dataclasses import dataclass

from google import genai
from google.genai.errors import APIError, ClientError, ServerError

from config import Settings

logger = logging.getLogger(__name__)

# Every Flash/Flash-Lite model with no announced shutdown date, ordered by
# preference: newest-and-cheapest first, then the older generation, then the
# one that is merely expensive. gemini-3.1-flash-lite is deliberately absent
# because it has a hard 2027-05-07 shutdown, inside our support window.
#
# Rate limits are applied per Google Cloud PROJECT, not per API key, so extra
# keys only add capacity when they come from separate projects. Whether each
# model also gets its own quota bucket is not published; check the live numbers
# at https://aistudio.google.com/rate-limit before relying on model fallback
# alone to buy you headroom.
FALLBACK_MODELS = (
    "gemini-3.7-flash",       # $0.75/$3.75, released Aug 2026
    "gemini-3.6-flash",       # $0.75/$3.75, released Jul 2026
    "gemini-3.5-flash-lite",  # $0.30/$2.50, released Jul 2026
    "gemini-2.5-flash",       # $0.30/$2.50, previous generation
    "gemini-2.5-flash-lite",  # $0.10/$0.40, cheapest available
    "gemini-3.5-flash",       # $1.50/$9.00, last resort: 2x the price of 3.7
)

# Conversation titles are a throwaway four-word summary, so they run on the
# cheapest model in the chain rather than the one answering tenant questions.
TITLE_MODEL = "gemini-3.5-flash-lite"

TITLE_INSTRUCTION = (
    "You write short titles for conversations with a NYC tenant-rights "
    "assistant. Given the opening exchange, reply with a title of at most four "
    "words naming the tenant's specific issue. Reply with the title only: no "
    "quotes, no trailing punctuation, no 'Title:' prefix. "
    "Examples: Broken Heat Complaint. Illegal Eviction Notice. "
    "Security Deposit Return."
)

MAX_TITLE_WORDS = 4
MAX_TITLE_CHARS = 60

# A transient 5xx is worth retrying on the same pair; a quota or 404 error is
# not, because the answer will not change within the request.
MAX_TRANSIENT_RETRIES = 2
BACKOFF_SECONDS = 0.5


def _error_code(error: APIError) -> int | None:
    """Best-effort HTTP status extraction across SDK versions."""

    for attribute in ("code", "status_code", "status"):
        value = getattr(error, attribute, None)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def _status_name(error: APIError) -> str:
    """Return the SDK's symbolic status (NOT_FOUND, RESOURCE_EXHAUSTED, ...)."""

    return str(getattr(error, "status", "") or "").upper()


def _is_model_unavailable(error: APIError) -> bool:
    """True when the model itself is gone, e.g. after a Google deprecation."""

    if _error_code(error) == 404 or _status_name(error) == "NOT_FOUND":
        return True
    return "not found" in str(error).lower()


def _is_quota_error(error: APIError) -> bool:
    """True when this key is rate limited or has exhausted its quota."""

    if _error_code(error) == 429 or _status_name(error) == "RESOURCE_EXHAUSTED":
        return True
    text = str(error).lower()
    return "resource_exhausted" in text or "quota" in text or "rate limit" in text


def _clean_title(raw: str) -> str:
    """Trim a model-generated title down to something the sidebar can show."""

    # Models like to wrap titles in quotes or prefix them with "Title:", and
    # occasionally ignore the word limit, so normalize rather than trust.
    title = raw.strip().strip('"“”\'')
    title = re.sub(r"^title\s*:\s*", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+", " ", title).strip(" .!?,;:")

    words = title.split(" ")
    if len(words) > MAX_TITLE_WORDS:
        title = " ".join(words[:MAX_TITLE_WORDS])

    return title[:MAX_TITLE_CHARS].strip()


@dataclass
class AIService:
    """Owns one Gemini client per configured API key."""

    clients: tuple[genai.Client, ...] = ()
    initialization_error: str | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> "AIService":
        if not settings.gemini_api_keys:
            return cls(initialization_error="Gemini API key is missing.")

        clients: list[genai.Client] = []
        failures: list[str] = []

        for index, api_key in enumerate(settings.gemini_api_keys, start=1):
            try:
                clients.append(genai.Client(api_key=api_key))
            except Exception as exc:
                # One bad key should not disable chat when others still work.
                failures.append(f"key #{index}: {exc}")

        if not clients:
            return cls(initialization_error=f"Failed to create Gemini client: {'; '.join(failures)}")

        if failures:
            logger.warning("Some Gemini keys could not be initialized: %s", "; ".join(failures))

        return cls(clients=tuple(clients))

    def is_ready(self) -> bool:
        return bool(self.clients)

    def key_count(self) -> int:
        """Number of usable keys, so app.py can log the configured redundancy."""

        return len(self.clients)

    def _generate(self, model_names: tuple[str, ...], contents: list[dict], config: dict | None) -> str:
        """Try each model against each key until one combination answers.

        Failure handling differs by cause: a retired model is abandoned for every
        key at once, an exhausted key falls through to the next key and then the
        next model, and a transient 5xx is retried in place with backoff.
        """

        if not self.clients:
            raise RuntimeError("Gemini is not configured yet.")

        last_error: Exception | None = None

        for model_name in model_names:
            model_retired = False

            for key_index, client in enumerate(self.clients, start=1):
                for attempt in range(MAX_TRANSIENT_RETRIES):
                    try:
                        response = client.models.generate_content(
                            model=model_name,
                            contents=contents,
                            config=config,
                        )
                        return (response.text or "").strip()

                    except ClientError as exc:
                        last_error = exc

                        if _is_model_unavailable(exc):
                            # No other key can conjure up a retired model.
                            logger.warning("Model %s is unavailable; trying the next model.", model_name)
                            model_retired = True
                        elif _is_quota_error(exc):
                            logger.warning("Key #%s is rate limited on %s.", key_index, model_name)
                        else:
                            raise

                        break

                    except ServerError as exc:
                        last_error = exc
                        if attempt + 1 < MAX_TRANSIENT_RETRIES:
                            time.sleep(BACKOFF_SECONDS * (2 ** attempt))

                if model_retired:
                    break

        attempted = ", ".join(model_names)
        raise RuntimeError(
            f"Every Gemini model and key combination failed. Tried: {attempted}"
        ) from last_error

    def generate_reply(self, messages: list[dict[str, str]]) -> str:
        if not self.clients:
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

        if not contents:
            raise ValueError("No messages were provided for content generation.")

        return self._generate(FALLBACK_MODELS, contents, config) or "I could not generate a response."

    def generate_title(self, user_message: str, assistant_message: str = "") -> str:
        """Summarize the opening exchange into a short conversation title.

        Runs on the cheap model first but reuses the main chain as backup, so a
        naming call never fails just because the lite model is rate limited.
        """

        opening = f"Tenant: {user_message.strip()}"
        if assistant_message.strip():
            opening += f"\n\nAssistant: {assistant_message.strip()}"

        contents = [{"role": "user", "parts": [{"text": opening}]}]
        config = {"system_instruction": TITLE_INSTRUCTION}

        # TITLE_MODEL is usually already in FALLBACK_MODELS; dedupe so it is not
        # retried twice before moving on to a genuinely different model.
        chain = (TITLE_MODEL,) + tuple(m for m in FALLBACK_MODELS if m != TITLE_MODEL)

        return _clean_title(self._generate(chain, contents, config))
