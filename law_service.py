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
from dataclasses import dataclass, field
from datetime import date, datetime

logger = logging.getLogger(__name__)

# NYC Admin Code section numbers: "27-2029", "26-504.1", "27-2017.4", "8-107".
CITATION_RE = re.compile(r"^\d{1,3}-\d{1,5}(?:\.\d{1,3})?$")

# Numbers that name more than one section of the Admin Code.
AMBIGUOUS_CITATIONS = frozenset({"26-1301"})

ADMIN_CODE_CHIP_RE = re.compile(r"^NYC Admin Code § (\S+)$")

ALP_CODE_LIBRARY_URL = "https://codelibrary.amlegal.com/"  # only the per-record URL pattern is verified; link the site, not a guessed path

_COLUMNS = (
    "section_key,source_key,authority,citation,title,heading_path,full_text,history,notes,"
    "repealed,last_amended,last_checked_at,official_url,status"
)

# Upper bound on chips looked up for one building page; the page shows at
# most a few hundred violations, each citing one or two sections.
_MAX_LINK_LOOKUP = 200


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

    @property
    def is_current(self) -> bool:
        return self.status == "active" and not self.repealed


def is_valid_citation(citation: str) -> bool:
    return bool(CITATION_RE.fullmatch(citation or ""))


def get_sections(client, citation: str) -> list[LawSection]:
    """Every library section with this number, current ones first.

    Usually one. Raises LawUnavailable if the library cannot be read.
    """
    if client is None:
        raise LawUnavailable("no database client")
    try:
        response = client.table("legal_sources").select(_COLUMNS).eq("citation", citation).execute()
        rows = list(response.data or [])
    except Exception as exc:  # noqa: BLE001 -- any read failure is "unavailable"
        raise LawUnavailable(str(exc)) from exc
    sections = [s for s in (_to_section(row) for row in rows) if s is not None]
    sections.sort(key=lambda s: (not s.is_current, s.section_key))
    return sections


def linkable_citations(client, citations) -> set[str]:
    """Which of these section numbers can be linked unambiguously.

    One query for the whole page. Never raises: a failure means no links.
    """
    wanted = sorted({c for c in citations if is_valid_citation(c) and c not in AMBIGUOUS_CITATIONS})
    if client is None or not wanted:
        return set()
    wanted = wanted[:_MAX_LINK_LOOKUP]
    try:
        response = (
            client.table("legal_sources")
            .select("citation,status,repealed")
            .in_("citation", wanted)
            .eq("status", "active")
            .execute()
        )
        rows = list(response.data or [])
    except Exception:  # noqa: BLE001
        logger.warning("Legal library unavailable for chip links.", exc_info=True)
        return set()
    counts: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("repealed"):
            continue
        cite = row.get("citation")
        if isinstance(cite, str):
            counts[cite] = counts.get(cite, 0) + 1
    return {cite for cite, n in counts.items() if n == 1}


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
