"""Daily usage counts with nothing in them that could point at a person.

WHY THIS EXISTS (owner, 2026-10-11)
To answer "what do tenants use, where do they get stuck, did an update
help?" without watching anyone. The only thing ever stored is

    (day, event, one value from a fixed list) -> how many times

WHAT CAN NEVER BE RECORDED, BY CONSTRUCTION
record() takes an event name and one dimension value, and drops the call
unless BOTH are on the allowlist below. There is no parameter for a user
id, a session, an IP address, a URL or any text a tenant typed, so there
is nothing to forget to strip. The database function that stores the
counts (record_usage, see the migration) checks the same allowlist again,
so a caller that bypasses this module still can't write free text.

  * No identifiers: no user id, session id, visitor id, IP, hash of any
    of those, cookie or fingerprint.
  * No free text: no chat content, titles, names, emails, streets,
    addresses, apartment numbers, ZIP codes, or URLs.
  * One dimension per counter, per day. Never "borough x outcome x hour":
    combinations of rare values are how aggregate data re-identifies
    people. Reports hide any count under 5 (supabase/checks).
  * Chat content is out of scope entirely: only THAT a message was sent.

HOW IT IS STORED
Counts are added up in memory and written in one database call when
FLUSH_AFTER_EVENTS have built up or FLUSH_AFTER_SECONDS have passed, on a
background thread, so no page waits on it. A restart loses whatever had
not been written yet; that is acceptable for counts (same stance as
rate_limit.py). If the write fails (including before the migration is
applied), the counts are dropped and nothing is retried for a while.

RESPECTING "DO NOT TRACK ME"
A browser that sends Global Privacy Control (Sec-GPC: 1) is not counted
at all; usage_routes.py checks it before calling record().
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import Counter

import area_service
import branding
import citation_guard
import law_service
import resources as resources_data

logger = logging.getLogger(__name__)

MAX_NAME_LENGTH = 40
FLUSH_AFTER_EVENTS = 50
FLUSH_AFTER_SECONDS = 60
# After a failed write, don't try again for this long.
RETRY_AFTER_SECONDS = 300
MAX_ROWS_PER_WRITE = 200
RETENTION_MONTHS = 12
# Reports never show a count smaller than this.
SMALL_COUNT = 5

YES_NO = frozenset({"yes", "no"})

# Flask endpoint -> the page name that is counted. Never the URL.
PAGES = {
    "main.home": "home", "main.area_lookup": "area", "main.building_lookup": "building",
    "main.law_index": "law_index", "main.law_section": "law", "main.resources": "resources",
    "main.learn_more": "learn_more", "main.privacy": "privacy", "main.chat": "chat",
    "main.settings": "settings", "main.login": "login", "main.signup": "signup",
    "main.forgot_password": "forgot_password", "main.reset_password": "reset_password",
    "main.complete_account": "account_complete", "main.review_form": "form_review",
}
PAGE_NAMES = frozenset(PAGES.values())

LANGUAGES = frozenset({"en", "es", "zh", "ru", "bn", "ar", "ht", "fr", "ko", "other"})
STATUS_CODES = frozenset({"400", "401", "403", "404", "405", "413", "429", "500", "502", "503", "other"})
CHIPS = frozenset({"look_up_street", "free_help", "repairs", "eviction", "deposit", "continue_building"})


def slug(name: str) -> str:
    """'Housing Court Answers' -> 'housing_court_answers' (capped)."""
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")[:MAX_NAME_LENGTH]


# Outbound links on the Resources page, named by the fixed lists they come from.
RESOURCE_IDS = frozenset(
    slug(item["name"])
    for item in (
        *resources_data.REPORT_LINKS,
        *branding.HELP_RESOURCES,
        *(court for courts in resources_data.HOUSING_COURTS.values() for court in courts),
    )
)

# event -> the only values its one dimension may take.
EVENTS: dict[str, frozenset[str]] = {
    "page_view": PAGE_NAMES,
    "view_device": frozenset({"mobile", "desktop"}),
    "view_language": LANGUAGES,
    "view_referrer": frozenset({"search", "social", "direct", "internal", "other"}),
    "view_signed_in": YES_NO,
    "area_search": frozenset({"found", "no_match", "invalid", "error"}),
    "area_borough": frozenset(area_service.BOROUGHS),
    "area_cross_used": YES_NO,
    "area_zip_used": YES_NO,
    "building_lookup": frozenset({"found", "not_found", "invalid", "error"}),
    "resource_click": RESOURCE_IDS,
    "chip_click": CHIPS,
    "chat": frozenset({"message_sent", "reply_with_citations", "conversation_created",
                       "conversation_archived", "conversation_deleted"}),
    "guard_violation": frozenset(citation_guard.VIOLATION_KINDS),
    "ai_error": frozenset({"unavailable", "failed"}),
    "ra81": frozenset({"draft_started", "reviewed", "downloaded"}),
    "signup": frozenset({"view", "success"}),
    "account_complete": frozenset({"saved", "skipped"}),
    "login": frozenset({"failure"}),
    "error_route": PAGE_NAMES | {"other"},
    "error_status": STATUS_CODES,
}
# The one event whose values are a shape rather than a list: a section of
# public law ("27-2029"). Public, not personal, and checked by pattern.
PATTERN_EVENTS = {"law_view": law_service.is_valid_citation}

_NAME_RE = re.compile(r"^[a-z0-9_]{1,%d}$" % MAX_NAME_LENGTH)

_lock = threading.Lock()
_buffer: Counter = Counter()
_state = {"pending": 0, "since": 0.0, "failed_at": 0.0}


def is_allowed(event, dim) -> bool:
    if not isinstance(event, str) or not isinstance(dim, str) or len(dim) > MAX_NAME_LENGTH:
        return False
    if event in EVENTS:
        return dim in EVENTS[event]
    if event in PATTERN_EVENTS:
        return bool(PATTERN_EVENTS[event](dim))
    return False


def record(event: str, dim: str) -> bool:
    """Count one occurrence. False (and nothing kept) unless both are allowlisted."""
    if not is_allowed(event, dim):
        return False
    with _lock:
        if not _state["pending"]:
            _state["since"] = time.monotonic()
        _buffer[(event, dim)] += 1
        _state["pending"] += 1
    return True


def pending() -> dict[tuple[str, str], int]:
    with _lock:
        return dict(_buffer)


def clear() -> None:
    with _lock:
        _buffer.clear()
        _state.update(pending=0, since=0.0, failed_at=0.0)


def due(now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    with _lock:
        if not _state["pending"]:
            return False
        if _state["failed_at"] and now - _state["failed_at"] < RETRY_AFTER_SECONDS:
            return False
        return _state["pending"] >= FLUSH_AFTER_EVENTS or now - _state["since"] >= FLUSH_AFTER_SECONDS


def flush(client) -> int:
    """Write what has built up, in one call. Returns how many rows were sent.

    Never raises. On failure the counts are dropped rather than kept
    growing in memory, and no write is attempted for RETRY_AFTER_SECONDS.
    """
    with _lock:
        rows = [{"event": event, "dim": dim, "n": n} for (event, dim), n in _buffer.items()]
        _buffer.clear()
        _state.update(pending=0, since=0.0)
    if not rows or client is None:
        return 0
    try:
        for start in range(0, len(rows), MAX_ROWS_PER_WRITE):
            client.rpc("record_usage", {"p_rows": rows[start:start + MAX_ROWS_PER_WRITE]}).execute()
    except Exception as exc:  # noqa: BLE001 -- counting must never break or slow a page
        logger.warning("Usage counts could not be saved (%s).", type(exc).__name__)
        with _lock:
            _state["failed_at"] = time.monotonic()
        return 0
    with _lock:
        _state["failed_at"] = 0.0
    return len(rows)


def flush_if_due(client, in_background: bool = True) -> None:
    if not due():
        return
    if in_background:
        threading.Thread(target=flush, args=(client,), name="usage-flush", daemon=True).start()
    else:
        flush(client)
