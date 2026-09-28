"""Looks up what the City of New York already knows about a building.

WHY THIS IS THE FEATURE THAT MATTERS
A general-purpose chatbot can explain tenant law. It cannot tell a tenant
that their own building has three open Class C violations, one of them for
heat, logged in February and still open. That needs live public data, and
this module is where the site gets it. It is the first thing on the site
that is impossible to get from ChatGPT, and it works before anyone signs
up -- which inverts the old order of "make an account, then find out if
this is useful".

WHERE THE DATA COMES FROM (both free, neither needs an API key)
  1. NYC Planning's GeoSearch turns whatever the tenant typed -- "231 echo
     pl bx" -- into a normalised address plus a BBL (borough-block-lot),
     the city's stable identifier for a tax lot.
  2. NYC Open Data's Housing Maintenance Code Violations dataset
     (wvxf-dwi5), published by HPD and updated daily, is queried by that
     BBL. Querying by BBL rather than by street name matters: HPD writes
     "ECHO PLACE", tenants write "Echo Pl", and a string match between the
     two silently finds nothing -- which would tell a tenant with a
     dangerous building that it was clean.

HONESTY RULES THIS MODULE ENFORCES
  - The matched address is always returned and always shown, so a wrong
    geocode is visible ("is this your building?") rather than silent.
  - The data's own "last updated" date comes from the city's dataset
    metadata, not from our clock. When it cannot be fetched, the report
    says the date is unknown rather than guessing.
  - "Certified" is surfaced separately from "open". A certification means
    the LANDLORD told HPD the problem is fixed. HPD's own guidance is that
    tenants are notified of certifications and can challenge them, which
    triggers a re-inspection. A tenant looking at a certified violation for
    a problem that is plainly still there needs to know that.

FAILURE BEHAVIOUR
Every network failure raises LookupUnavailable, which the route turns into
a plain-language "the city's data service didn't respond" page. Nothing
here is allowed to 500 the page: this is the front door of the site, and
the city's API having a bad afternoon must not look like our site being
broken.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

GEOSEARCH_URL = "https://geosearch.planninglabs.nyc/v2/search"
VIOLATIONS_URL = "https://data.cityofnewyork.us/resource/wvxf-dwi5.json"
VIOLATIONS_METADATA_URL = "https://data.cityofnewyork.us/api/views/wvxf-dwi5.json"
HPD_CLEAR_VIOLATIONS_URL = "https://www.nyc.gov/site/hpd/services-and-information/clear-violations.page"
HPD_VIOLATIONS_DATASET_URL = "https://data.cityofnewyork.us/Housing-Development/Housing-Maintenance-Code-Violations/wvxf-dwi5"

REQUEST_TIMEOUT_SECONDS = 6.0

# How long a building's report is reused before the city is asked again.
# The dataset itself only updates daily, so an hour costs nothing in
# freshness and keeps one popular address from hammering a free API.
CACHE_TTL_SECONDS = 3600
_CACHE_MAX_ENTRIES = 512

# Upper bound on open violations fetched for one building. The worst
# buildings in the city carry several hundred open at once; beyond this the
# page would be unreadable anyway, and the counts come from a separate
# aggregate query so they stay exact even when the list is capped.
MAX_OPEN_VIOLATIONS = 1000

# How far back "history" reaches. Two years is long enough to show a
# pattern and short enough that it reflects the current owner's conduct.
HISTORY_WINDOW_DAYS = 730

MAX_ADDRESS_LENGTH = 200
MAX_APARTMENT_LENGTH = 20

CLASS_ORDER = ("C", "B", "A", "I")

# Wording follows HPD's "Clear Violations" page. The correction periods are
# stated only where that page states them plainly; everything else links
# out rather than paraphrasing a legal deadline.
CLASS_INFO = {
    "C": {
        "name": "Immediately hazardous",
        "short": "Class C",
        "deadline": "Most must be corrected within 24 hours. Some conditions, such as lead-based paint, window guards, mold and pests, have 21 days.",
    },
    "B": {
        "name": "Hazardous",
        "short": "Class B",
        "deadline": "30 days to correct from the notice date.",
    },
    "A": {
        "name": "Non-hazardous",
        "short": "Class A",
        "deadline": "90 days to correct from the notice date.",
    },
    "I": {
        "name": "Information order",
        "short": "Class I",
        "deadline": "An order to the owner to provide information or take an administrative step.",
    },
}

_BBL_RE = re.compile(r"^[1-5]\d{9}$")
# Citation parsing for HPD violation text.
#
# Measured against 5,000 real open violations (Sept 2026): HPD writes the
# legal basis in at least forty different shapes -- "§ 27-2005 ADM CODE",
# "§ 27-2005 HMC:", "HMC ADM CODE: § 27-2017.4", "§ 27-2005(B)(2)(B) HMC,
# § 11-52, § 11-53 RCNY", "§ 301, § 302; § 309 (1) ( C) MDL AND DEPT. RULES
# AND REGULATIONS.", "28 RCNY § 25-101; & 30 (2)(B) MDL; NYC FIRE CODE ..."
# -- and the description BODY cites further rules of its own.
#
# The first version of this labelled every "§ NN-NN" it found as the NYC
# Administrative Code. On real data that put a FALSE statute citation on
# 1,348 of 5,000 violations: "28 RCNY § 11-06" (a city rule) shown as
# "NYC Admin Code § 11-06". On a site whose whole point is not making false
# citations, the rule is now: label a citation only when its source is
# unambiguous, and drop it otherwise. A missing chip costs nothing; a
# mislabelled one is a false statement about the law.
#
#   Housing Maintenance Code  27-2001 .. 27-2199: unambiguous by number --
#                             nothing else in these texts is numbered 27-2xxx.
#   Rules of the City of NY   only when the text itself says RCNY next to
#                             the number; "28 RCNY" keeps its title, a bare
#                             "RCNY" does not get one guessed.
#   Multiple Dwelling Law     bare 1-3 digit sections, and only inside the
#                             leading citation clause, and only when that
#                             clause names the MDL. Never from the body,
#                             which is full of bare numbers ("APT 5", "3rd").
_HMC_RE = re.compile(r"\b(27-2\d{3}(?:\.\d+)?)")
_RCNY_TITLED_RE = re.compile(r"\b(\d{1,2})\s*RCNY\s*(?:§+\s*)?((?:\d+-\d+(?:\.\d+)?)(?:\s*(?:,|and|&)\s*§*\s*\d+-\d+(?:\.\d+)?)*)", re.I)
# A run of section numbers: the first carries §, later ones may not
# ("§ 12-06, 12-10 RCNY"), and any may carry subdivisions ("(B)(5)").
_SECTION_RUN = r"§+\s*\d{1,2}-\d+(?:\.\d+)?(?:\s*\([^)]*\))*(?:\s*(?:,|and|&)\s*§*\s*\d{1,2}-\d+(?:\.\d+)?(?:\s*\([^)]*\))*)*"
_RCNY_TRAILING_RE = re.compile(r"(" + _SECTION_RUN + r")\s*,?\s*RCNY\b", re.I)
# The text naming the Administrative Code right after the numbers makes a
# non-HMC section (e.g. "§ 26-1103 ADMIN. CODE:") unambiguous too.
_ADMIN_TRAILING_RE = re.compile(r"(" + _SECTION_RUN + r")\s*,?\s*(?:HMC|ADM(?:IN)?\.?\s*CODE)\b", re.I)
_SECTION_NUMBER_IN_RE = re.compile(r"\d+-\d+(?:\.\d+)?")
_MDL_MARKER_RE = re.compile(r"M\s*/\s*D\s*LAW|\bM/D\b|\bMDL\b|MULTIPLE\s+DWELLING", re.I)
_MDL_SECTION_RE = re.compile(r"(?<![\d\-.])§*\s*(\d{1,3}(?:-[A-Z])?)(?![\d\-.])(?!\s*RCNY)(?!\s*(?:ST|ND|RD|TH)\b)", re.I)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

# Words that belong to the citation clause rather than the description.
_CLAUSE_WORDS = {
    "§", "§§", "ADM", "ADM.", "ADMIN", "ADMIN.", "CODE", "CODE:", "CODE.", "HMC", "HMC:", "HMC,",
    "M/D", "LAW", "LAW:", "LAW.", "MDL", "MDL:", "MDL;", "RCNY", "RCNY:", "RCNY;", "AND", "&",
    "DEPT", "DEPT.", "DEPARTMENT", "RULES", "REGULATIONS", "REGULATIONS.", "REGS", "REGS.",
    "NYC", "FIRE", "OF", "-", "–", "—", ":", ";", ",",
}
# A clause token: section numbers and punctuation, optionally carrying
# glued subdivisions like "§27-2045(B)(5)," or "(7)(B)" -- short
# parenthesised groups, never a parenthesised WORD.
_CLAUSE_TOKEN_RE = re.compile(r"^[§\d\-.,;:()&/]*(?:\([A-Z0-9]{1,3}\)[§\d\-.,;:()&/]*)*$|^\(?[A-Z]\)?[,;:]?$")
_UNIT_TOKEN_RE = re.compile(r"\b(?=[a-z]*\d)(?=\d*[a-z])[a-z\d]{1,4}\b")
_ORDINAL_RE = re.compile(r"^\d+(st|nd|rd|th)$")


class LookupUnavailable(Exception):
    """The city's data service could not be reached or answered nonsense."""


class AddressNotFound(Exception):
    """GeoSearch answered, but found no NYC building at that address."""


class InvalidAddress(ValueError):
    """The input cannot be an address (empty, too long, no house number)."""


@dataclass(frozen=True)
class BuildingMatch:
    label: str
    borough: str
    bbl: str
    bin: str | None


@dataclass
class Violation:
    violation_id: str
    violation_class: str
    description: str
    raw_description: str
    citations: list[str]
    status: str
    status_date: date | None
    inspection_date: date | None
    apartment: str
    story: str
    rent_impairing: bool
    certified: bool


@dataclass
class BuildingReport:
    match: BuildingMatch
    open_violations: list[Violation]
    open_counts: dict[str, int]
    history_counts: dict[str, int]
    rent_impairing_open: int
    certified_open: int
    apartment: str | None
    apartment_violations: list[Violation]
    data_as_of: date | None
    list_truncated: bool
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def open_total(self) -> int:
        return sum(self.open_counts.values())

    @property
    def history_total(self) -> int:
        return sum(self.history_counts.values())

    def open_by_class(self) -> list[tuple[str, dict, list[Violation]]]:
        grouped = []
        for cls in CLASS_ORDER:
            items = [v for v in self.open_violations if v.violation_class == cls]
            if items:
                grouped.append((cls, CLASS_INFO[cls], items))
        return grouped


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------


def clean_address(raw: str | None) -> str:
    text = re.sub(r"\s+", " ", (raw or "")).strip()
    if not text:
        raise InvalidAddress("Enter a street address.")
    if len(text) > MAX_ADDRESS_LENGTH:
        raise InvalidAddress("That address is too long.")
    if not re.search(r"\d", text):
        # Every NYC street address has a house number, and GeoSearch
        # happily resolves a bare street name to SOME building on it --
        # which would show a tenant the wrong building's violations.
        raise InvalidAddress("Include the building number, e.g. 231 Echo Place, Bronx.")
    return text


def normalize_apartment(raw: str | None) -> str | None:
    """'Apt. 4b', '#4B', 'unit 4-B' -> '4B'. None when blank."""
    if not raw:
        return None
    text = raw.strip().upper()[:MAX_APARTMENT_LENGTH]
    text = re.sub(r"^(APARTMENT|APT\.?|UNIT|#)\s*", "", text)
    text = re.sub(r"[^A-Z0-9]", "", text)
    return text or None


def is_valid_bbl(value: str | None) -> bool:
    return bool(value) and bool(_BBL_RE.match(value))


# ---------------------------------------------------------------------------
# Presentation helpers
# ---------------------------------------------------------------------------


def _split_clause(description: str) -> tuple[str, str]:
    """Split HPD text into (leading citation clause, description body)."""
    text = _CONTROL_RE.sub(" ", description or "")
    text = re.sub(r"\s+", " ", text).strip()
    tokens = text.split(" ")
    i = 0
    while i < len(tokens):
        token = tokens[i]
        bare = token.upper().strip()
        if bare in _CLAUSE_WORDS or _CLAUSE_TOKEN_RE.match(bare):
            i += 1
            continue
        # "ADM" glued to punctuation, "CODE:-" and similar.
        if re.sub(r"[^A-Z/]", "", bare) in {"ADM", "ADMIN", "CODE", "HMC", "MD", "M/D", "LAW", "MDL", "RCNY", "DEPT", "REGS", "REGULATIONS", "RULES"}:
            i += 1
            continue
        break
    clause = " ".join(tokens[:i])
    body = " ".join(tokens[i:])
    return clause, body


def extract_citations(description: str) -> list[str]:
    """The laws and rules a violation cites, labelled only when unambiguous."""
    text = _CONTROL_RE.sub(" ", description or "")
    clause, _ = _split_clause(text)
    found: list[str] = []

    for number in _HMC_RE.findall(text):
        found.append(f"NYC Admin Code § {number}")
    for group in _ADMIN_TRAILING_RE.findall(text):
        for number in _SECTION_NUMBER_IN_RE.findall(group):
            found.append(f"NYC Admin Code § {number}")

    for title, numbers in _RCNY_TITLED_RE.findall(text):
        for number in _SECTION_NUMBER_IN_RE.findall(numbers):
            found.append(f"{int(title)} RCNY § {number}")
    for group in _RCNY_TRAILING_RE.findall(text):
        for number in _SECTION_NUMBER_IN_RE.findall(group):
            if _HMC_RE.fullmatch(number):
                continue
            label = f"NYC Rules (RCNY) § {number}"
            if not any(existing.endswith(f"RCNY § {number}") for existing in found):
                found.append(label)

    if _MDL_MARKER_RE.search(clause):
        scrubbed = _HMC_RE.sub(" ", clause)
        scrubbed = re.sub(r"\b\d{1,2}\s*RCNY\b.*?(?:;|$)", " ", scrubbed, flags=re.I)
        scrubbed = re.sub(r"FIRE\s+CODE.*?(?:;|$)", " ", scrubbed, flags=re.I)
        scrubbed = re.sub(r"\([^)]*\)", " ", scrubbed)
        for number in _MDL_SECTION_RE.findall(scrubbed):
            found.append(f"Multiple Dwelling Law § {number.upper()}")

    return list(dict.fromkeys(found))


_KEEP_UPPER = {"hpd", "nyc", "dob", "dep", "fdny", "ecb", "hmc", "mdl", "rcny", "c/o", "co", "hvac", "gfci"}


def readable_description(description: str) -> str:
    """HPD writes violations in capitals with the legal basis up front.
    Keep the words, drop the shouting and the citation clause (shown
    separately as chips), keep unit identifiers like 4B and a few agency
    acronyms upper-case, and never leave leading punctuation behind."""
    clause, body = _split_clause(description)
    text = body or clause
    text = re.sub(r"^[\s\-–—:;,.]+", "", text).strip()
    if not text:
        return ""
    lowered = text.lower()

    def restore_unit(match: re.Match) -> str:
        token = match.group(0)
        return token if _ORDINAL_RE.match(token) else token.upper()

    lowered = _UNIT_TOKEN_RE.sub(restore_unit, lowered)
    lowered = re.sub(
        r"\b(" + "|".join(re.escape(w) for w in _KEEP_UPPER) + r")\b",
        lambda m: m.group(0).upper(),
        lowered,
    )
    return lowered[:1].upper() + lowered[1:]


# HPD's status strings, as a tenant would say them. The one that forced
# this: "NOV SENT OUT" title-cased to "Nov Sent Out", which sits right next
# to an inspection date on the page and reads as the month of November.
# NOV is HPD's abbreviation for Notice of Violation.
_STATUS_LABELS = {
    "NOV SENT OUT": "Notice sent to owner",
    "INFO NOV SENT OUT": "Information notice sent to owner",
    "NOV CERTIFIED LATE": "Owner certified fixed (late)",
    "NOV CERTIFIED ON TIME": "Owner certified fixed",
    "NOT COMPLIED WITH": "Not complied with",
    "VIOLATION OPEN": "Open",
    "VIOLATION REOPEN": "Reopened",
    "INVALID CERTIFICATION": "Owner's certification rejected",
    "CERTIFICATION POSTPONMENT GRANTED": "Owner granted more time",
    "CERTIFICATION POSTPONEMENT GRANTED": "Owner granted more time",
    "FIRST NO ACCESS TO RE- INSPECT VIOLATION": "Inspector could not get access",
    "FIRST NO ACCESS TO RE-INSPECT VIOLATION": "Inspector could not get access",
    "SECOND NO ACCESS TO RE-INSPECT VIOLATION": "Inspector could not get access (twice)",
    "DEFECT LETTER ISSUED": "Owner's paperwork rejected",
}


def readable_status(raw: str) -> str:
    """Tenant-readable status. Unknown values are sentence-cased with NOV
    spelled out, so no future status can reintroduce the November bug."""
    key = re.sub(r"\s+", " ", (raw or "").strip().upper())
    if not key:
        return "Status unknown"
    if key in _STATUS_LABELS:
        return _STATUS_LABELS[key]
    text = re.sub(r"\bNOV\b", "NOTICE OF VIOLATION", key).lower()
    return text[:1].upper() + text[1:]


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "")).date()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _get_json(url: str, params: dict | None = None):
    full = f"{url}?{urllib.parse.urlencode(params)}" if params else url
    headers = {"Accept": "application/json", "User-Agent": "SideKick-Tidbit-building-lookup"}
    token = os.environ.get("NYC_OPEN_DATA_APP_TOKEN", "").strip()
    if token and url.startswith("https://data.cityofnewyork.us/"):
        # Optional. Unauthenticated requests work but share a throttled
        # pool; a free app token gets this app its own limit.
        headers["X-App-Token"] = token
    request = urllib.request.Request(full, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LookupUnavailable(str(exc)) from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise LookupUnavailable(f"Unreadable response: {exc}") from exc


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


class _TTLCache:
    """Tiny in-process cache. Per worker, lost on restart -- which is fine,
    because every entry can be rebuilt from public data in one request."""

    def __init__(self, ttl: float, max_entries: int):
        self._ttl = ttl
        self._max = max_entries
        self._items: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def get(self, key: str):
        with self._lock:
            item = self._items.get(key)
            if not item:
                return None
            stored_at, value = item
            if time.monotonic() - stored_at > self._ttl:
                self._items.pop(key, None)
                return None
            return value

    def put(self, key: str, value) -> None:
        with self._lock:
            if len(self._items) >= self._max:
                oldest = min(self._items, key=lambda k: self._items[k][0])
                self._items.pop(oldest, None)
            self._items[key] = (time.monotonic(), value)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


_geocode_cache = _TTLCache(CACHE_TTL_SECONDS, _CACHE_MAX_ENTRIES)
_violations_cache = _TTLCache(CACHE_TTL_SECONDS, _CACHE_MAX_ENTRIES)
_metadata_cache = _TTLCache(CACHE_TTL_SECONDS, 4)


def clear_caches() -> None:
    _geocode_cache.clear()
    _violations_cache.clear()
    _metadata_cache.clear()


# ---------------------------------------------------------------------------
# The lookups
# ---------------------------------------------------------------------------


def geocode(address: str) -> BuildingMatch:
    text = clean_address(address)
    cache_key = text.lower()
    cached = _geocode_cache.get(cache_key)
    if cached is not None:
        return cached

    payload = _get_json(GEOSEARCH_URL, {"text": text, "size": 5})
    features = (payload or {}).get("features") if isinstance(payload, dict) else None
    if not isinstance(features, list):
        raise LookupUnavailable("GeoSearch returned an unexpected shape.")

    for feature in features:
        props = (feature or {}).get("properties") or {}
        pad = ((props.get("addendum") or {}).get("pad") or {})
        bbl = str(pad.get("bbl") or "").strip()
        if not is_valid_bbl(bbl):
            # Not a PAD building (a park, a neighbourhood, a street). Only
            # a real tax lot has violations to show.
            continue
        match = BuildingMatch(
            label=str(props.get("label") or props.get("name") or text),
            borough=str(props.get("borough") or ""),
            bbl=bbl,
            bin=str(pad.get("bin")) if pad.get("bin") else None,
        )
        _geocode_cache.put(cache_key, match)
        return match

    raise AddressNotFound(text)


def dataset_as_of() -> date | None:
    """The city's own 'rows last updated' date. None if unavailable --
    never our clock standing in for theirs."""
    cached = _metadata_cache.get("as_of")
    if cached is not None:
        return cached or None
    try:
        meta = _get_json(VIOLATIONS_METADATA_URL)
        stamp = int((meta or {}).get("rowsUpdatedAt"))
        as_of = datetime.fromtimestamp(stamp, tz=timezone.utc).date()
    except (LookupUnavailable, TypeError, ValueError, OverflowError):
        logger.warning("Could not read the violations dataset's update date.")
        _metadata_cache.put("as_of", False)
        return None
    _metadata_cache.put("as_of", as_of)
    return as_of


def _building_where(bbl: str) -> str:
    """SoQL filter matching every violation HPD recorded for this lot.

    `bbl` alone is not enough. Measured on the live dataset (Sept 2026):
    bbl is populated on 99.88% of open violations, and the gaps are not a
    lag that fills in later -- they cluster by BUILDING. One sampled
    building had no bbl on any of its violations at all, so a bbl-only
    query told that tenant "no open violations on record". HPD's own
    boroid/block/lot fields are populated on 100% of rows, so they are
    OR-ed in. That is strictly additive: no building can lose results.

    Residual gap, stated honestly: a condo UNIT recorded under its own unit
    lot (e.g. lot 1102) with no bbl still cannot be matched from the
    building's base lot. docs/frontend/building-lookup.md records this.
    """
    if not is_valid_bbl(bbl):
        raise ValueError(f"Refusing to query a malformed BBL: {bbl!r}")
    boroid, block, lot = bbl[0], str(int(bbl[1:6])), str(int(bbl[6:10]))
    return f"(bbl='{bbl}' OR (boroid='{boroid}' AND block='{block}' AND lot='{lot}'))"


def _fetch_open(bbl: str) -> list[dict]:
    if not is_valid_bbl(bbl):
        # BBL is interpolated into a SoQL string below. This check is what
        # makes that safe: ten digits, first one 1-5, nothing else.
        raise ValueError(f"Refusing to query a malformed BBL: {bbl!r}")
    rows = _get_json(
        VIOLATIONS_URL,
        {
            "$select": ",".join(
                [
                    "violationid", "class", "novdescription", "currentstatus",
                    "currentstatusdate", "inspectiondate", "apartment", "story",
                    "rentimpairing",
                ]
            ),
            "$where": f"{_building_where(bbl)} AND violationstatus='Open'",
            "$order": "inspectiondate DESC",
            "$limit": str(MAX_OPEN_VIOLATIONS),
        },
    )
    if not isinstance(rows, list):
        raise LookupUnavailable("Violations query returned an unexpected shape.")
    return [row for row in rows if isinstance(row, dict)]


def _fetch_counts(bbl: str, where_extra: str) -> dict[str, int]:
    if not is_valid_bbl(bbl):
        raise ValueError(f"Refusing to query a malformed BBL: {bbl!r}")
    rows = _get_json(
        VIOLATIONS_URL,
        {
            "$select": "class, count(*) AS n",
            "$where": f"{_building_where(bbl)} AND {where_extra}",
            "$group": "class",
        },
    )
    counts = {cls: 0 for cls in CLASS_ORDER}
    if not isinstance(rows, list):
        raise LookupUnavailable("Count query returned an unexpected shape.")
    for row in rows:
        if not isinstance(row, dict):
            continue
        cls = str(row.get("class") or "").strip().upper()
        try:
            n = int(row.get("n") or 0)
        except (TypeError, ValueError):
            continue
        if cls in counts:
            counts[cls] += n
    return counts


def _to_violation(row: dict) -> Violation:
    raw = str(row.get("novdescription") or "")
    status = str(row.get("currentstatus") or "").strip()
    return Violation(
        violation_id=str(row.get("violationid") or ""),
        violation_class=str(row.get("class") or "").strip().upper(),
        description=readable_description(raw),
        raw_description=raw,
        citations=extract_citations(raw),
        status=readable_status(status),
        status_date=_parse_date(row.get("currentstatusdate")),
        inspection_date=_parse_date(row.get("inspectiondate")),
        apartment=str(row.get("apartment") or "").strip(),
        story=str(row.get("story") or "").strip(),
        rent_impairing=str(row.get("rentimpairing") or "").strip().upper() == "Y",
        certified="CERTIF" in status.upper(),
    )


def _class_rank(v: Violation) -> int:
    return CLASS_ORDER.index(v.violation_class) if v.violation_class in CLASS_ORDER else len(CLASS_ORDER)


def lookup(address: str, apartment: str | None = None) -> BuildingReport:
    """Everything the page shows, in one call. Raises InvalidAddress,
    AddressNotFound or LookupUnavailable -- never anything else from the
    network."""
    match = geocode(address)
    apt = normalize_apartment(apartment)

    cached = _violations_cache.get(match.bbl)
    if cached is None:
        since = (date.today() - timedelta(days=HISTORY_WINDOW_DAYS)).isoformat()
        open_rows = _fetch_open(match.bbl)
        open_counts = _fetch_counts(match.bbl, "violationstatus='Open'")
        history_counts = _fetch_counts(match.bbl, f"inspectiondate >= '{since}'")
        cached = (open_rows, open_counts, history_counts)
        _violations_cache.put(match.bbl, cached)
    open_rows, open_counts, history_counts = cached

    violations = sorted(
        (_to_violation(row) for row in open_rows),
        key=lambda v: (_class_rank(v), -(v.inspection_date or date.min).toordinal()),
    )

    apartment_violations = []
    if apt:
        apartment_violations = [v for v in violations if normalize_apartment(v.apartment) == apt]

    return BuildingReport(
        match=match,
        open_violations=violations,
        open_counts=open_counts,
        history_counts=history_counts,
        rent_impairing_open=sum(1 for v in violations if v.rent_impairing),
        certified_open=sum(1 for v in violations if v.certified),
        apartment=apt,
        apartment_violations=apartment_violations,
        data_as_of=dataset_as_of(),
        list_truncated=len(open_rows) >= MAX_OPEN_VIOLATIONS,
    )


# ---------------------------------------------------------------------------
# Hand-off to the chat
# ---------------------------------------------------------------------------

CHAT_PROMPT_MAX_CHARS = 1500


def chat_prompt(report: BuildingReport) -> str:
    """The first message a tenant sends when they click "talk this through".

    It is plain text the tenant SEES as their own first message -- not a
    hidden system prompt -- so they know exactly what the assistant was
    told, and can edit the next message if the match was wrong.
    """
    lines = [f"I live at {report.match.label}."]
    if report.apartment:
        lines[0] = f"I live at {report.match.label}, apartment {report.apartment}."

    as_of = report.data_as_of.isoformat() if report.data_as_of else "a recent date"
    if report.open_total == 0:
        lines.append(f"NYC HPD's public data (as of {as_of}) shows no open violations for my building.")
    else:
        parts = [
            f"{report.open_counts[c]} Class {c}" for c in CLASS_ORDER if report.open_counts.get(c)
        ]
        lines.append(
            f"NYC HPD's public data (as of {as_of}) shows {report.open_total} open violations "
            f"for my building: {', '.join(parts)}."
        )
        focus = report.apartment_violations or report.open_violations
        label = "In my apartment" if report.apartment_violations else "The most serious include"
        examples = []
        for v in focus[:3]:
            cite = f" ({'; '.join(v.citations)})" if v.citations else ""
            examples.append(f"Class {v.violation_class}: {v.description[:160]}{cite}")
        if examples:
            lines.append(f"{label}: " + " | ".join(examples))
        if report.certified_open:
            lines.append(
                f"{report.certified_open} of them the landlord has certified as corrected."
            )
    lines.append("What do these mean for me, and what should I do next?")

    prompt = " ".join(lines)
    if len(prompt) > CHAT_PROMPT_MAX_CHARS:
        prompt = prompt[: CHAT_PROMPT_MAX_CHARS - 60].rsplit(" ", 1)[0] + " ... What should I do next?"
    return prompt
