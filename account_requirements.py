"""What an account needs on file, and asking for it when it is missing.

WHY THIS EXISTS
Features get added after accounts exist. When sign-up started asking for
a name and date of birth, every account made before that day had
neither, and nothing ever asked them. This module is the general fix: a
feature that needs something from the tenant declares it here once, and
every existing account is asked for it the next time it signs in (or the
next time it opens the app, for sessions that were already signed in).
New accounts that provide it at sign-up are never asked.

HOW TO ADD ONE (see docs/frontend/account-requirements.md)
1. Write a Requirement below: how to tell it is on file (is_met), whether
   it can be collected right now (can_collect), and how to turn the
   submitted form into user_metadata (collect).
2. Add a template partial templates/_requirement_<key>.html with its
   fields (no HTML/CSS/JS comments, like every template).
3. Append it to REQUIREMENTS. Adding or re-versioning one changes
   REQUIREMENTS_VERSION, which makes every signed-in session re-check on
   its next page load -- nobody has to log out for it to take effect.

WHAT IT NEVER DOES
It never leaves someone stuck. If a requirement can't be collected (no
encryption key configured), it is not asked. If saving fails, the tenant
continues and is asked again next time. Optional requirements have
"Remind me next time"; required ones (like the age check) don't.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

import crypto_service
import profile_service

SESSION_KEY = "account_requirements"
SKIPPED_KEY = "account_requirements_skipped"


class RequirementError(ValueError):
    """Shown to the tenant as-is."""


@dataclass(frozen=True)
class Collected:
    metadata: dict
    session_updates: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Requirement:
    key: str
    version: int
    title: str
    why: str
    template: str
    required: bool
    is_met: Callable[[dict | None], bool]
    can_collect: Callable[[], bool]
    collect: Callable[[dict, date], Collected]


def _profile_collect(form: dict, today: date) -> Collected:
    try:
        profile = profile_service.validate(
            form.get("first_name", ""), form.get("last_name", ""), form.get("date_of_birth", ""), today=today
        )
    except profile_service.ProfileError as exc:
        raise RequirementError(str(exc)) from None
    metadata = profile_service.to_metadata(profile)
    if metadata is None:
        raise RequirementError("Your name can't be saved right now.")
    return Collected(metadata, {"first_name": profile.first_name})


PROFILE = Requirement(
    key="profile",
    version=1,
    title="Your name and date of birth",
    why="We now ask everyone for their name, to greet you, and date of birth, to confirm you're at least "
        f"{profile_service.MIN_AGE}. Your account was made before we asked.",
    template="_requirement_profile.html",
    # The age check is the point, so there's no "remind me next time".
    required=True,
    is_met=lambda metadata: profile_service.read_metadata(metadata) is not None,
    # Stored only encrypted, so without a key it can't be collected at all.
    can_collect=crypto_service.is_enabled,
    collect=_profile_collect,
)

REQUIREMENTS: tuple[Requirement, ...] = (PROFILE,)

REQUIREMENTS_VERSION = hashlib.sha256(
    ",".join(f"{r.key}:{r.version}" for r in REQUIREMENTS).encode()
).hexdigest()[:12]

# Pages that wait for the requirements. Public pages, Settings (so the
# tenant can always reach account deletion and export), logout and the
# requirements page itself are never gated.
GATED_ENDPOINTS = frozenset({"main.chat"})


def by_key(key: str) -> Requirement | None:
    return next((r for r in REQUIREMENTS if r.key == key), None)


def missing(metadata: dict | None) -> list[Requirement]:
    """Requirements this account doesn't meet and that can be collected now."""
    result = []
    for requirement in REQUIREMENTS:
        try:
            if requirement.can_collect() and not requirement.is_met(metadata):
                result.append(requirement)
        except Exception:  # noqa: BLE001 -- a broken check must not lock anyone out
            continue
    return result


def record(session, metadata: dict | None) -> None:
    """Store what this account is missing, for this version of the list."""
    session[SESSION_KEY] = {"v": REQUIREMENTS_VERSION, "missing": [r.key for r in missing(metadata)]}


# After a failed check, try again this much later rather than on every
# page (auth being down shouldn't add a slow call to each request) or
# only at the next login (one blip shouldn't skip the check for days).
RETRY_UNKNOWN_SECONDS = 600


def record_unknown(session) -> None:
    """Couldn't read the account: let the tenant through, and check again later."""
    session[SESSION_KEY] = {"v": REQUIREMENTS_VERSION, "missing": [], "unknown_at": time.time()}


def is_current(session) -> bool:
    state = session.get(SESSION_KEY)
    if not isinstance(state, dict) or state.get("v") != REQUIREMENTS_VERSION:
        return False
    unknown_at = state.get("unknown_at")
    return not unknown_at or time.time() - float(unknown_at) < RETRY_UNKNOWN_SECONDS


def pending(session) -> list[Requirement]:
    """What to ask for now: missing, minus what was skipped this session."""
    state = session.get(SESSION_KEY) or {}
    skipped = set(session.get(SKIPPED_KEY) or [])
    result = []
    for key in state.get("missing") or []:
        requirement = by_key(key)
        if requirement and key not in skipped:
            result.append(requirement)
    return result


def mark_met(session, keys) -> None:
    state = dict(session.get(SESSION_KEY) or {})
    state["missing"] = [k for k in state.get("missing") or [] if k not in set(keys)]
    session[SESSION_KEY] = state


def skip(session, keys) -> None:
    """Don't ask again this session. Next login asks again."""
    session[SKIPPED_KEY] = sorted(set(session.get(SKIPPED_KEY) or []) | set(keys))
