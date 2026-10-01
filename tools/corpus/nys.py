"""New York State laws from the NY Senate Open Legislation API.

    GET https://legislation.nysenate.gov/api/3/laws/{LAW}?full=true&key=KEY

One request per law returns its whole tree WITH section text (RPP is
~1.6 MB). The sources in registry.NYS_SOURCES each name the article (and
title) they want from that tree. The shape below was read from real
responses captured on 2026-10-01, not from the docs:

    result.documents                     the law (docType CHAPTER)
      .documents.items[]                 ARTICLE  locationId "A7", "A6-A", ...
        .documents.items[]               TITLE    "A7T1" (GOB) or SECTION
          locationId "235-B", title, text, repealed, activeDate

Text arrives hard-wrapped at ~72 columns with the newlines as the two
characters backslash and n. A line indented by two spaces starts a new
paragraph ("  § 235-b. ...", "  2. ...", "  (a) ..."); the rest are
continuations. Lines starting "* NB" are the publisher's notes ("* NB
Repealed June 15, 2034" -- Good Cause's sunset), kept as notes, never as
law text. A note closes the "* "-marked block above it, and a block whose
note says it is not in force today (a future version) is left out of the
law text: see split_text.

The key is never logged: errors are reported without the URL.
Standard library only, like the rest of tools/corpus.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date

from tools.corpus.alp import ParseResult
from tools.corpus.model import Section

API_BASE = "https://legislation.nysenate.gov/api/3/laws"
OFFICIAL_URL = "https://www.nysenate.gov/legislation/laws/{law}/{location}"
TIMEOUT = 180
MAX_BYTES = 40 * 1024 * 1024  # RPP is ~1.6 MB


class NysError(RuntimeError):
    pass


def fetch_law_tree(law_id: str, key: str, opener=urllib.request.urlopen, user_agent: str = "") -> dict:
    """The full tree of one law, with text. Raises NysError (never with the key in it)."""
    if not re.fullmatch(r"[A-Z]{3}", law_id or ""):
        raise NysError(f"bad law id {law_id!r}")
    query = urllib.parse.urlencode({"full": "true", "key": key})
    request = urllib.request.Request(f"{API_BASE}/{law_id}?{query}", headers={"User-Agent": user_agent or "tools.corpus"})
    try:
        with opener(request, timeout=TIMEOUT) as response:
            raw = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise NysError(f"NY Senate API {law_id} -> HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise NysError(f"NY Senate API {law_id} failed: {reason}".replace(key, "<key>")) from None
    if len(raw) > MAX_BYTES:
        raise NysError(f"NY Senate API {law_id}: response over {MAX_BYTES} bytes; refusing")
    try:
        body = json.loads(raw)
    except ValueError:
        raise NysError(f"NY Senate API {law_id}: response is not JSON") from None
    if not body.get("success"):
        raise NysError(f"NY Senate API {law_id}: {str(body.get('message'))[:200]}".replace(key, "<key>"))
    return body


@dataclass(frozen=True)
class Scope:
    """Where in a law's tree a source lives, e.g. ("A7",) or ("A7", "A7T1")."""

    path: tuple[str, ...]


def _find(node: dict, path: tuple[str, ...]) -> dict | None:
    for location in path:
        children = ((node.get("documents") or {}).get("items")) or []
        node = next((c for c in children if c.get("locationId") == location), None)
        if node is None:
            return None
    return node


def _sections_under(node: dict) -> list[dict]:
    out = []
    for child in ((node.get("documents") or {}).get("items")) or []:
        if child.get("docType") == "SECTION":
            out.append(child)
        else:
            out.extend(_sections_under(child))
    return out


_NOTE_RE = re.compile(r"^\*\s*NB\b")
_STAR_RE = re.compile(r"^\*\s+")
_MONTHS = {m: i for i, m in enumerate(
    ("january february march april may june july august september october november december").split(), 1)}
_DATE_RE = r"([A-Z][a-z]+)\s+(\d{1,2}),\s*(\d{4})"


def _note_date(text: str) -> date | None:
    match = re.fullmatch(_DATE_RE, text.strip().rstrip("."))
    if not match or match.group(1).lower() not in _MONTHS:
        return None
    try:
        return date(int(match.group(3)), _MONTHS[match.group(1).lower()], int(match.group(2)))
    except ValueError:
        return None


def in_force(note: str, today: date) -> bool:
    """Whether the text a publisher's note closes is the law today.

    "Effective June 15, 2034" on a block not yet in force -> False;
    "Effective until June 15, 2034" / "Effective X until Y" -> in force
    until Y; "Repealed June 15, 2034" -> in force until then (a sunset).
    Anything else ("There are 2 sub 5's") -> True.
    """
    body = re.sub(r"^NB\s+", "", note.strip())
    match = re.fullmatch(r"Effective\s+(?:(.+?)\s+)?until\s+(.+)", body)
    if match:
        start, end = (_note_date(match.group(1)) if match.group(1) else None), _note_date(match.group(2))
        return (start is None or start <= today) and (end is None or today < end)
    match = re.fullmatch(r"Effective\s+(.+)", body)
    if match:
        start = _note_date(match.group(1))
        return start is None or start <= today
    match = re.fullmatch(r"Repealed\s+(.+)", body)
    if match:
        end = _note_date(match.group(1))
        return end is None or today < end
    return True


def split_text(raw: str, today: date | None = None) -> tuple[list[str], list[str]]:
    """(paragraphs in force today, notes) from the API's hard-wrapped text.

    The API prints every dated version of a provision: a block that starts
    with "* " and is closed by a note such as "* NB Effective until June
    15, 2034", followed by the next version closed by "* NB Effective June
    15, 2034" (RPAPL § 711 and RPL § 226-c carry Good Cause's 2034
    versions). Only the text in force on `today` is kept as law; a version
    not yet (or no longer) in force is dropped and noted.
    """
    today = today or date.today()
    text = (raw or "").replace("\\n", "\n").replace("\r", "")
    paragraphs: list[list[str]] = []
    starred: list[bool] = []
    notes: list[str] = []
    dropped = 0
    for line in text.split("\n"):
        if not line.strip():
            continue
        stripped = line.strip()
        if _NOTE_RE.match(stripped):
            note = _STAR_RE.sub("", stripped)
            notes.append(note)
            # The note closes the starred block above it.
            block_start = len(paragraphs)
            while block_start > 0 and not starred[block_start - 1]:
                block_start -= 1
            if block_start > 0:
                block_start -= 1
                if not in_force(note, today):
                    dropped += len(paragraphs) - block_start
                    del paragraphs[block_start:], starred[block_start:]
                else:
                    starred[block_start] = False  # closed; not a block start any more
            continue
        if line.startswith("  ") or not paragraphs:
            is_star = bool(_STAR_RE.match(stripped))
            paragraphs.append([_STAR_RE.sub("", stripped)])
            starred.append(is_star)
        else:
            paragraphs[-1].append(stripped)
    joined = []
    for lines in paragraphs:
        para = ""
        for part in lines:
            if not para:
                para = part
            elif para.endswith("-") and not para.endswith(" -"):
                para += part  # "rent-" + "stabilized" stays one word
            else:
                para += " " + part
        joined.append(re.sub(r"\s{2,}", " ", para))
    if dropped:
        notes.append(f"{dropped} paragraph(s) of a version not in force on {today.isoformat()} are not shown")
    return joined, notes


def citation_of(location_id: str) -> str:
    """'235-B' -> '235-b' (how the statutes write it); '7-108' unchanged."""
    return location_id.lower()


def _history(active_date: str | None) -> list[str]:
    """One line the model's date parser understands (model.latest_effective_date)."""
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", active_date or "")
    if not match:
        return []
    year, month, day = match.groups()
    return [f"Text in force in this form since {active_date} per NY Senate Open Legislation "
            f"(eff. {int(month)}/{int(day)}/{year})"]


def parse_law(tree: dict, *, law_id: str, law_name: str, source_key: str, authority: str,
              scope: tuple[str, ...], today: date | None = None) -> ParseResult:
    """Sections of one source (an article or title) from a full law tree."""
    root = ((tree.get("result") or {}).get("documents")) or {}
    node = _find(root, scope)
    if node is None:
        return ParseResult(sections=[], warnings=[f"{'/'.join(scope)} not found in {law_id}"])
    heading = f"{law_name} > Article {node.get('docLevelId') or scope[0]}: {node.get('title', '').strip()}"
    if len(scope) > 1:
        heading = f"{law_name} > {' > '.join(scope)}: {node.get('title', '').strip()}"
    sections, warnings, seen = [], [], set()
    for doc in _sections_under(node):
        location = doc.get("locationId") or ""
        citation = citation_of(location)
        if not citation or citation in seen:
            warnings.append(f"skipped duplicate or empty location {location!r}")
            continue
        seen.add(citation)
        paragraphs, notes = split_text(doc.get("text") or "", today)
        repealed = bool(doc.get("repealed"))
        sections.append(Section(
            source_key=source_key,
            authority=authority,
            citation=citation,
            title=(doc.get("title") or "").strip(),
            heading_path=heading,
            paragraphs=[] if repealed else paragraphs,
            official_url=OFFICIAL_URL.format(law=law_id, location=urllib.parse.quote(location)),
            history=_history(doc.get("activeDate")),
            notes=notes,
            repealed=repealed,
            jurisdiction="NYS",
        ))
    return ParseResult(sections=sections, warnings=warnings)
