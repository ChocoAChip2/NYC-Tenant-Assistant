"""Asking for feedback at a good moment, and keeping the answer.

WHY (owner, 2026-11-08)
"A feature where feedback can be taken after we have analyzed a good time
to ask: when the session is prolonged and seemingly coming to an end."
Asking too early or in the middle of someone's problem is how a feedback
prompt gets ignored, or worse, gets in the way of a tenant in trouble.

WHEN THE CHAT ASKS (deterministic; no model call)
  * Never in a short chat. The question only comes once the tenant has
    sent MIN_TURNS_TO_ASK messages in that conversation.
  * "Coming to an end", said: the tenant's latest message is a short
    sign-off ("thanks, that's all", "got it", "gracias") with no question
    in it. The question appears a moment after the reply.
  * "Coming to an end", unsaid: in a longer chat (MIN_TURNS_IDLE), the
    tenant has stopped typing for IDLE_SECONDS after a reply, with the tab
    in view. Typing again cancels it.
  * Not while a reply is pending, not twice on one page, and not again
    for a while once answered or dismissed: the answer sets a cookie for
    SNOOZE_DAYS[action]. "Don't ask again" lasts a year.

WHAT IS KEPT
A rating (helpful / somewhat / not helpful), an optional comment
(encrypted, at most MAX_COMMENT characters), what prompted the question
and the page language, under the day only. Not the account, not the
conversation, not the time of day. Only through submit_feedback()
(supabase/migrations/*_feedback.sql), deleted after 12 months.
"""

from __future__ import annotations

import re

import crypto_service

MIN_TURNS_TO_ASK = 3
MIN_TURNS_IDLE = 5
IDLE_SECONDS = 90
COOKIE = "feedback_asked"
SNOOZE_DAYS = {"sent": 30, "later": 3, "never": 365}
RATINGS = {"helpful": 3, "somewhat": 2, "not_helpful": 1}
TRIGGERS = frozenset({"closing", "idle"})
MAX_COMMENT = 1000
MAX_SIGN_OFF_LENGTH = 100

_SIGN_OFF_RE = re.compile(
    r"(?:\bthanks?\b|\bthank (?:you|u)\b|\bthx\b|\bty\b|\bappreciate it\b|\bthat'?s (?:all|it|everything|helpful)\b"
    r"|\bthat is (?:all|it|everything)\b|\b(?:that|this) helps?\b|\bgot it\b|\bbye\b|\bgood ?bye\b|\bnothing else\b"
    r"|\bno more questions\b|\bgracias\b|\bmerci\b|\basante\b|谢谢|謝謝|多谢)",
    re.IGNORECASE,
)


def is_sign_off(message: str | None) -> bool:
    """A short goodbye or thank-you with no new question in it."""
    text = (message or "").strip()
    if not text or len(text) > MAX_SIGN_OFF_LENGTH or "?" in text or "？" in text:
        return False
    return bool(_SIGN_OFF_RE.search(text.replace("’", "'")))


def offer(user_turns: int, latest_message: str | None, asked_recently: bool) -> dict:
    """What the chat page should do after this reply.

    now: show the question shortly after the reply.
    idle_seconds: show it if the tenant hasn't typed for this long (0 = don't).
    """
    if asked_recently or user_turns < MIN_TURNS_TO_ASK:
        return {"now": False, "idle_seconds": 0}
    if is_sign_off(latest_message):
        return {"now": True, "idle_seconds": 0}
    return {"now": False, "idle_seconds": IDLE_SECONDS if user_turns >= MIN_TURNS_IDLE else 0}


class Invalid(ValueError):
    pass


def submission(payload: dict) -> dict | None:
    """The database call's arguments for a "sent" answer; None for later/never.

    Raises Invalid for anything malformed. A comment is kept only if it
    can be encrypted, because the page says it will be.
    """
    action = payload.get("action")
    if action not in SNOOZE_DAYS:
        raise Invalid("action")
    if action != "sent":
        return None
    rating = RATINGS.get(payload.get("rating"))
    trigger = payload.get("trigger")
    if rating is None or trigger not in TRIGGERS:
        raise Invalid("rating or trigger")
    comment = payload.get("comment")
    comment = re.sub(r"\s+", " ", comment).strip()[:MAX_COMMENT] if isinstance(comment, str) else ""
    return {
        "p_rating": rating,
        "p_comment": crypto_service.encrypt(comment) if comment and crypto_service.is_enabled() else None,
        "p_trigger": trigger,
    }
