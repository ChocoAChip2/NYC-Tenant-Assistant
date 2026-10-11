"""The legal library, read side: one section of official law, and chip links.

The library is loaded by tools/corpus (official American Legal Publishing
XML) into `legal_sources`, which anon can read on purpose: public law, and
a citation should resolve without an account. This module only reads it,
with the app's existing anon client.

Two honesty rules shape it:

  * A section that has left the official code is SHOWN, with a notice,
    never hidden. The refresh never deletes (old citations point at it),
    so a tenant following an old link learns it is no longer current
    instead of hitting a dead end or, worse, reading it as live law.

  * A violation chip links to a section only when that is unambiguous:
    exactly one active, unrepealed section with that number. Title 26 of
    the Admin Code has two § 26-1301s (Right to Counsel and Certification
    of Certain Rent Payment); the library holds one of them, so a bare
    "§ 26-1301" is never auto-linked. Same principle as the chip labels
    themselves (see building_service.extract_citations).

Any failure reading the library -- including the library not existing yet,
before the 20260929 migration -- means "no links" on the building page and
a plain 503 on /law, never a broken page.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import date, datetime

from tools.corpus import registry

logger = logging.getLogger(__name__)

# Section numbers as the official publishers number them. NYC Admin Code:
# "27-2029", "26-504.1", "27-2056.6.1", "8-102a" (the first version missed
# the last two shapes; review, 2026-09-30). NY State law (since 2026-10-01):
# "235-b", "226-c", "711", "7-108" -- the first two shapes 404'd on the live
# site until this allowed them. ASCII only, so other scripts' digits never
# reach the query.
CITATION_RE = re.compile(
    r"^\d{1,3}(?:-\d{1,5}[a-z]?(?:\.\d{1,3}){0,2}|-[a-z]{1,2})?$", re.ASCII
)

# Who publishes each jurisdiction's official text (shown on /law pages).
PUBLISHERS = {
    "NYC": "American Legal Publishing, the City's official publisher",
    "NYS": "the New York State Senate's Open Legislation service",
}
NYS_LAWS_URL = "https://www.nysenate.gov/legislation/laws/CONSOLIDATED"

# The library is read with the app's shared client, whose HTTP timeout is
# the library default (120 s). A page must never wait that long on it:
# every read runs with its own short deadline, and the building page's chip
# links come from an in-memory index of the whole library (a few hundred
# rows) refreshed at most hourly, so a building page normally makes no
# library call at all. The /law index page lists from the same index.
READ_TIMEOUT_SECONDS = 4.0
INDEX_TTL_SECONDS = 3600
INDEX_RETRY_SECONDS = 60  # after a failed load, don't make every page wait again

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="law-read")
_index_lock = threading.Lock()
_index: dict = {"counts": None, "rows": None, "loaded_at": 0.0, "failed_at": 0.0}
_INDEX_COLUMNS = "citation,title,source_key,status,repealed,jurisdiction"

# The /law index groups sections by law: City first, then State, in the
# order the registry lists them. The public sees the law's name, never its
# source key.
_LAWS = {s.key: s for s in (*registry.ALP_SOURCES, *registry.NYS_SOURCES)}
_LAW_ORDER = {key: position for position, key in enumerate(_LAWS)}
JURISDICTION_NAMES = {"NYC": "City law", "NYS": "State law"}
_CITATION_PARTS_RE = re.compile(r"\d+|[a-z]+")

# Numbers that name more than one section of the Admin Code.
AMBIGUOUS_CITATIONS = frozenset({"26-1301"})

ADMIN_CODE_CHIP_RE = re.compile(r"^NYC Admin Code § (\S+)$")

ALP_CODE_LIBRARY_URL = "https://codelibrary.amlegal.com/"  # only the per-record URL pattern is verified; link the site, not a guessed path

_COLUMNS = (
    "section_key,source_key,authority,citation,title,heading_path,full_text,history,notes,"
    "repealed,last_amended,last_checked_at,official_url,status,jurisdiction"
)

class LawUnavailable(Exception):
    """The library could not be read (Supabase down, or not set up yet)."""


@dataclass
class LawSection:
    section_key: str
    source_key: str
    authority: str
    citation: str
    title: str
    heading_path: str
    paragraphs: list[str]
    official_url: str
    history: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    repealed: bool = False
    last_amended: date | None = None
    last_checked_at: datetime | None = None
    status: str = "active"
    jurisdiction: str = "NYC"

    @property
    def publisher(self) -> str:
        return PUBLISHERS.get(self.jurisdiction, PUBLISHERS["NYC"])

    @property
    def is_current(self) -> bool:
        return self.status == "active" and not self.repealed


@dataclass(frozen=True)
class IndexEntry:
    """One line of the /law index: a section's number and title."""

    citation: str
    title: str
    repealed: bool = False
    status: str = "active"


@dataclass
class LawGroup:
    """One law on the /law index, e.g. the Housing Maintenance Code."""

    name: str
    detail: str
    jurisdiction: str
    entries: list[IndexEntry] = field(default_factory=list)


def is_valid_citation(citation: str) -> bool:
    return bool(CITATION_RE.fullmatch(citation or ""))


def normalize_citation(raw: str) -> str | None:
    """"§ 27-2029", "§27-2029", "27-2029." -> "27-2029"; None if still not a citation."""
    tidy = (raw or "").strip().lstrip("§").strip().rstrip(".").strip()
    return tidy if tidy != raw and is_valid_citation(tidy) else None


def _read(fn):
    """Run one library read with a deadline. Raises LawUnavailable on timeout or error."""
    future = _executor.submit(fn)
    try:
        return future.result(timeout=READ_TIMEOUT_SECONDS)
    except FutureTimeout as exc:
        raise LawUnavailable(f"library read timed out after {READ_TIMEOUT_SECONDS}s") from exc
    except Exception as exc:  # noqa: BLE001 -- any read failure is "unavailable"
        raise LawUnavailable(str(exc)) from exc


def clear_cache() -> None:
    with _index_lock:
        _index.update(counts=None, rows=None, loaded_at=0.0, failed_at=0.0)


def get_sections(client, citation: str) -> list[LawSection]:
    """Every library section with this number, current ones first.

    Usually one. Raises LawUnavailable if the library cannot be read.
    """
    if client is None:
        raise LawUnavailable("no database client")
    response = _read(lambda: client.table("legal_sources").select(_COLUMNS).eq("citation", citation).execute())
    rows = list(getattr(response, "data", None) or [])
    sections = [s for s in (_to_section(row) for row in rows) if s is not None]
    sections.sort(key=lambda s: (not s.is_current, s.section_key))
    return sections


def linkable_citations(client, citations) -> set[str]:
    """Which of these section numbers can be linked unambiguously.

    Answered from the hourly in-memory index. Never raises and never waits
    longer than one bounded read: a failure means no links.
    """
    wanted = {c for c in citations if is_valid_citation(c) and c not in AMBIGUOUS_CITATIONS}
    if not wanted:
        return set()
    counts = _active_counts(client)
    if counts is None:
        return set()
    return {cite for cite in wanted if counts.get(cite) == 1}


def _active_counts(client) -> dict[str, int] | None:
    """citation -> number of active, unrepealed sections with that number."""
    return _library_index(client)[0]


def get_index(client) -> list[LawGroup]:
    """Every section the library holds, grouped by law, for the /law index.

    City laws first, then State, in registry order; sections in number
    order. Repealed sections and ones that left the code are included (the
    page labels them). Answered from the hourly in-memory index; raises
    LawUnavailable if the library has never been read and can't be now.
    """
    rows = _library_index(client)[1]
    if rows is None:
        raise LawUnavailable("no library index")
    groups: dict[tuple[str, str], LawGroup] = {}
    for row in rows:
        law = _LAWS.get(row["source_key"])
        jurisdiction = law.jurisdiction if law else row["jurisdiction"]
        key = (jurisdiction, law.key if law else "")
        if key not in groups:
            name, _, detail = (law.name if law else "Other sections").partition(" (")
            groups[key] = LawGroup(name=name, detail=detail.rstrip(")"), jurisdiction=jurisdiction)
        groups[key].entries.append(IndexEntry(row["citation"], row["title"], row["repealed"], row["status"]))
    for group in groups.values():
        group.entries.sort(key=lambda e: _citation_order(e.citation))
    ordered = sorted(groups, key=lambda k: (k[0] != "NYC", k[0], _LAW_ORDER.get(k[1], len(_LAW_ORDER))))
    return [groups[key] for key in ordered]


def _citation_order(citation: str) -> list[tuple[int, int, str]]:
    """Number order, so § 27-2005 sorts before § 27-2017.1 and § 235-b after § 235-a."""
    return [(0, int(part), "") if part.isdigit() else (1, 0, part) for part in _CITATION_PARTS_RE.findall(citation)]


def _library_index(client) -> tuple[dict[str, int] | None, list[dict] | None]:
    """(citation -> count of active, unrepealed sections; every row, for /law).

    One bounded read of the whole library, kept for an hour.
    """
    now = time.monotonic()
    with _index_lock:
        counts, rows = _index["counts"], _index["rows"]
        loaded_at, failed_at = _index["loaded_at"], _index["failed_at"]
    if counts is not None and now - loaded_at < INDEX_TTL_SECONDS:
        return counts, rows
    if client is None or now - failed_at < INDEX_RETRY_SECONDS:
        return counts, rows  # a stale index beats no links
    try:
        response = _read(lambda: client.table("legal_sources")
                         .select(_INDEX_COLUMNS)
                         .limit(10000)
                         .execute())
    except LawUnavailable:
        logger.warning("Legal library unavailable for the section index.", exc_info=True)
        with _index_lock:
            _index["failed_at"] = now
        return counts, rows
    fresh: dict[str, int] = {}
    listing: list[dict] = []
    for row in getattr(response, "data", None) or []:
        if not isinstance(row, dict) or not isinstance(row.get("citation"), str):
            continue
        cite = row["citation"]
        status = str(row.get("status") or "active")
        repealed = bool(row.get("repealed"))
        listing.append({
            "citation": cite,
            "title": str(row.get("title") or ""),
            "source_key": str(row.get("source_key") or ""),
            "status": status,
            "repealed": repealed,
            "jurisdiction": "NYS" if row.get("jurisdiction") == "NYS" else "NYC",
        })
        if status == "active" and not repealed:
            fresh[cite] = fresh.get(cite, 0) + 1
    with _index_lock:
        _index.update(counts=fresh, rows=listing, loaded_at=now, failed_at=0.0)
    return fresh, listing


def admin_code_number(chip_label: str) -> str | None:
    """"NYC Admin Code § 27-2029" -> "27-2029"; anything else -> None."""
    match = ADMIN_CODE_CHIP_RE.match(chip_label or "")
    return match.group(1) if match and is_valid_citation(match.group(1)) else None


def _to_section(row) -> LawSection | None:
    if not isinstance(row, dict):
        return None
    try:
        url = str(row.get("official_url") or "")
        if not url.startswith("https://"):
            return None  # provenance is not optional
        return LawSection(
            section_key=str(row["section_key"]),
            source_key=str(row.get("source_key") or ""),
            authority=str(row.get("authority") or ""),
            citation=str(row["citation"]),
            title=str(row.get("title") or ""),
            heading_path=str(row.get("heading_path") or ""),
            paragraphs=[p for p in str(row.get("full_text") or "").split("\n") if p.strip()],
            official_url=url,
            history=[str(h) for h in (row.get("history") or []) if h],
            notes=[str(n) for n in (row.get("notes") or []) if n],
            repealed=bool(row.get("repealed")),
            last_amended=_parse_date(row.get("last_amended")),
            last_checked_at=_parse_datetime(row.get("last_checked_at")),
            status=str(row.get("status") or "active"),
            jurisdiction=str(row.get("jurisdiction") or "NYC"),
        )
    except (KeyError, TypeError, ValueError):
        logger.warning("Skipping a malformed legal_sources row.", exc_info=True)
        return None


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _parse_datetime(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
