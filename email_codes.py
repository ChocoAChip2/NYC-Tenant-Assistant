"""A 6-digit code emailed at sign-in, for accounts that turned it on.

WHY THE APP DOES THIS ITSELF
Supabase's built-in two-step sign-in offers authenticator apps and phone
numbers, not email. So the second step is enforced here, with Supabase's
emailed one-time codes: after a correct password, routes.login() keeps the
tenant signed OUT, sends a code, and only the session that verifying the
code returns becomes the browser session.

WHAT IT PROTECTS AGAINST
Someone who has only the password (leaked, reused, guessed) cannot sign in
through the site. It adds nothing if the tenant's inbox is compromised:
the password-reset link goes to the same inbox. And because Supabase does
not see this as a second factor, it is not enforced below the app.

WHERE THE SWITCH LIVES
user_metadata[METADATA_KEY], written through the tenant's own session
(supabase_service.update_user_metadata) and returned with every sign-in
response, so login() needs no extra request to read it.

Codes and tokens are never logged.

OFFERED ONLY ONCE EMAIL CAN CARRY A CODE
Turning codes ON is offered only when SIGN_IN_CODES_ENABLED is set (see
available()). Until the owner has set up the project's own mail server and
put {{ .Token }} in the "Magic link or OTP" template, Supabase either
refuses the email (its built-in sender only delivers to the project team)
or sends a link instead of a code, so the switch was a dead end (found
2026-10-11). An account that already has codes on keeps them, and can
still turn them off.
"""

from __future__ import annotations

import math
import os
import re
import time

METADATA_KEY = "email_code_signin"
CODE_LENGTH = 6

# Supabase refuses a second code to the same address inside 60 seconds.
RESEND_SECONDS = 60
# How long a half-finished sign-in (password accepted, code not yet
# entered) stays open. Codes themselves expire on Supabase's side.
PENDING_SECONDS = 15 * 60
# Wrong codes allowed for one Settings change before a new code is needed.
MAX_SETTINGS_ATTEMPTS = 5

SIGN_IN_KEY = "sign_in_code"
SETTINGS_KEY = "sign_in_code_change"

_CODE_RE = re.compile(r"\d{%d}" % CODE_LENGTH, re.ASCII)


def available() -> bool:
    """Whether Settings may offer to turn codes on (the owner's switch, set in the environment)."""
    return os.environ.get("SIGN_IN_CODES_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def is_on(metadata: dict | None) -> bool:
    return isinstance(metadata, dict) and metadata.get(METADATA_KEY) is True


def clean_code(raw: str | None) -> str | None:
    """The six digits someone typed (spaces and dashes forgiven), or None."""
    code = re.sub(r"[\s-]", "", raw or "")
    return code if _CODE_RE.fullmatch(code) else None


def seconds_until_resend(sent_at, now: float | None = None) -> int:
    try:
        remaining = float(sent_at) + RESEND_SECONDS - (time.time() if now is None else now)
    except (TypeError, ValueError):
        return 0
    return max(0, math.ceil(remaining))


def is_open(pending, now: float | None = None) -> bool:
    """Whether a pending step (a dict with "at") is still inside its window."""
    if not isinstance(pending, dict):
        return False
    try:
        age = (time.time() if now is None else now) - float(pending.get("at"))
    except (TypeError, ValueError):
        return False
    return 0 <= age <= PENDING_SECONDS
