"""What the city knows about a STREET -- no house number needed.

WHY THIS EXISTS (owner, 2026-10-03 / 2026-10-07)
Asking for an exact address up front scares people off. So the default
search is now a street name and a borough. From that alone the page can
say which community board, Council member, Assembly member and State
Senator cover the street, what kind of street it is (mostly apartment
buildings, zoned residential...), and list the buildings on it with their
open HPD violations -- so a tenant can tap their own building without ever
typing its number. The exact-address lookup (building_service) is still
there for anyone who wants it.

WHERE THE DATA COMES FROM (all free, none needs an API key)
  1. NYC Planning's GeoSearch turns "e 149th st" into the city's spelling
     of the street ("EAST 149 STREET"). Its answer is CHECKED against what
     was typed: GeoSearch fuzzy-matches, and "asdfgh street, queens"
     comes back as "211 STREET". A street whose words don't match is
     "not found", never a guess.
  2. MapPLUTO on NYC Open Data (one row per tax lot) gives every lot on the
     street with its Council district, community district, ZIP, police
     precinct, school district, land use, homes and zoning.
  3. NYC Planning's district maps answer "which Assembly / State Senate /
     Congressional district is this point in" for a sample of the lots.
  4. NYC Open Data's community board directory gives the board office.
  5. HPD's violations dataset, queried by street, gives open violations
     per building.
  Names of elected officials come from our own table (elected_officials),
  refreshed after elections by tools/officials -- never fetched per page.

LONG STREETS
Broadway in Manhattan crosses 8 Council districts. When a street spans
more than one district the report says so and the page asks for a ZIP
code or a cross street; either narrows the lots before anything is
counted. A cross street keeps only the lots within about two blocks of
where the two streets meet.

FAILURE BEHAVIOUR
Network trouble raises building_service.LookupUnavailable (same page
message as the building lookup). Optional extras (officials, district
maps, community board, violations) degrade to "not available" rather than
failing the page.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

import building_service
from building_service import LookupUnavailable, _get_json, _TTLCache

logger = logging.getLogger(__name__)

PLUTO_URL = "https://data.cityofnewyork.us/resource/64uk-42ks.json"
COMMUNITY_BOARDS_URL = "https://data.cityofnewyork.us/resource/ruf7-3wgc.json"
DISTRICT_LAYERS = {
    "assembly": ("https://services5.arcgis.com/GfwWNkhOj9bNBqoJ/arcgis/rest/services/NYC_State_Assembly_Districts/FeatureServer/0/query", "AssemDist"),
    "state_senate": ("https://services5.arcgis.com/GfwWNkhOj9bNBqoJ/arcgis/rest/services/NYC_State_Senate_Districts/FeatureServer/0/query", "StSenDist"),
    "congress": ("https://services5.arcgis.com/GfwWNkhOj9bNBqoJ/arcgis/rest/services/NYC_Congressional_Districts/FeatureServer/0/query", "CongDist"),
}
HOUSE_FIND_REP_URL = "https://www.house.gov/representatives/find-your-representative"

BOROUGHS = {
    # name shown -> (PLUTO code, HPD boroid, GeoSearch borough, community district prefix)
    "Manhattan": ("MN", "1", "Manhattan", "1"),
    "Bronx": ("BX", "2", "Bronx", "2"),
    "Brooklyn": ("BK", "3", "Brooklyn", "3"),
    "Queens": ("QN", "4", "Queens", "4"),
    "Staten Island": ("SI", "5", "Staten Island", "5"),
}

MAX_STREET_LENGTH = 80
MAX_LOTS = 5000           # Broadway in Manhattan is ~1,600 lots
MAX_BUILDINGS_SHOWN = 200
DISTRICT_SAMPLE_POINTS = 40
CROSS_STREET_METERS = 160  # about two short blocks
CACHE_TTL_SECONDS = 3600

LAND_USE = {
    "1": "one- and two-family homes",
    "2": "walk-up apartment buildings",
    "3": "elevator apartment buildings",
    "4": "mixed homes and shops",
    "5": "offices and stores",
    "6": "industrial buildings",
    "7": "transportation and utilities",
    "8": "public facilities and institutions",
    "9": "parks and open space",
    "10": "parking",
    "11": "vacant land",
}
RESIDENTIAL_LAND_USE = {"1", "2", "3", "4"}
ZONING_KINDS = {"R": "residential", "C": "commercial", "M": "manufacturing", "P": "park"}

_SUFFIXES = {
    "ST": "STREET", "STR": "STREET", "AVE": "AVENUE", "AV": "AVENUE", "PL": "PLACE", "RD": "ROAD",
    "BLVD": "BOULEVARD", "PKWY": "PARKWAY", "PKY": "PARKWAY", "DR": "DRIVE", "LN": "LANE", "CT": "COURT",
    "TER": "TERRACE", "TERR": "TERRACE", "SQ": "SQUARE", "HWY": "HIGHWAY", "EXPY": "EXPRESSWAY",
    "TPKE": "TURNPIKE", "CRES": "CRESCENT", "CIR": "CIRCLE", "WAY": "WAY", "LOOP": "LOOP",
}
_DIRECTIONS = {"E": "EAST", "W": "WEST", "N": "NORTH", "S": "SOUTH"}
_STREET_TYPES = set(_SUFFIXES.values()) | {"CONCOURSE", "ALLEY", "PATH", "WALK", "ROW", "SLIP", "PLAZA", "OVAL"}
_DIRECTION_WORDS = set(_DIRECTIONS.values())
_LEADING = {"ST": "SAINT", "MT": "MOUNT", "FT": "FORT"}
_SPELLED_ORDINALS = {
    "FIRST": "1", "SECOND": "2", "THIRD": "3", "FOURTH": "4", "FIFTH": "5", "SIXTH": "6", "SEVENTH": "7",
    "EIGHTH": "8", "NINTH": "9", "TENTH": "10", "ELEVENTH": "11", "TWELFTH": "12",
}
_HOUSE_NUMBER_RE = re.compile(r"^\d+[A-Z]?(?:-\d+[A-Z]?)?$")
_ZIP_RE = re.compile(r"^\d{5}$")
_SAFE_STREET_RE = re.compile(r"^[A-Z0-9][A-Z0-9 '\-.&/]{0,79}$")


class InvalidArea(ValueError):
    """The input can't be looked up (missing street, unknown borough, bad ZIP)."""


class StreetNotFound(Exception):
    """No street by that name in that borough."""


class CrossStreetNotFound(Exception):
    """The two streets don't meet (or the cross street wasn't found)."""


@dataclass
class Official:
    office: str
    district: int
    name: str | None
    url: str


@dataclass
class Building:
    bbl: str
    address: str
    homes: int
    open_counts: dict[str, int] = field(default_factory=dict)

    @property
    def open_total(self) -> int:
        return sum(self.open_counts.values())


@dataclass
class AreaReport:
    street: str                 # the city's spelling, e.g. "ECHO PLACE"
    borough: str                # "Bronx"
    zip_code: str | None
    cross_street: str | None    # the city's spelling of the cross street, if used
    lot_count: int
    homes: int
    land_use: list[tuple[str, int]]       # (plain-English label, lots), largest first
    zoning: str | None                    # "residential", "commercial"... (most lots)
    zoning_codes: list[str]
    council: list[int]
    community_districts: list[str]        # "205"
    zip_codes: list[str]
    precincts: list[str]
    school_districts: list[str]
    assembly: list[int]
    state_senate: list[int]
    congress: list[int]
    officials: list[Official]
    community_boards: list[dict]
    buildings: list[Building]
    buildings_total: int
    violations_available: bool
    officials_as_of: str | None
    truncated: bool = False

    @property
    def label(self) -> str:
        return f"{display_street(self.street)}, {self.borough}"

    @property
    def officials_as_of_label(self) -> str | None:
        """'2026-10-07' -> 'October 7, 2026'."""
        try:
            d = date.fromisoformat(self.officials_as_of or "")
        except ValueError:
            return None
        return f"{d.strftime('%B')} {d.day}, {d.year}"

    @property
    def spans_districts(self) -> bool:
        return any(len(x) > 1 for x in (self.council, self.community_districts, self.assembly, self.state_senate))

    @property
    def summary(self) -> str:
        """'Mostly elevator apartment buildings · about 560 homes'."""
        parts = []
        if self.land_use:
            top, count = self.land_use[0]
            parts.append(("Mostly " if count * 2 > self.lot_count else "Many ") + top)
        if self.homes:
            parts.append(f"about {self.homes:,} homes" if self.homes >= 20 else f"{self.homes} homes")
        return " · ".join(parts)


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------


def borough_from(raw: str | None) -> str:
    text = (raw or "").strip().lower()
    for name in BOROUGHS:
        if text == name.lower():
            return name
    aliases = {"new york": "Manhattan", "mn": "Manhattan", "bx": "Bronx", "bk": "Brooklyn", "kings": "Brooklyn",
               "qn": "Queens", "si": "Staten Island", "richmond": "Staten Island"}
    if text in aliases:
        return aliases[text]
    raise InvalidArea("Choose a borough.")


def clean_zip(raw: str | None) -> str | None:
    text = (raw or "").strip()
    if not text:
        return None
    if not _ZIP_RE.match(text) or not text.startswith(("100", "101", "102", "103", "104", "110", "111", "112", "113", "114", "116")):
        raise InvalidArea("Enter a 5-digit NYC ZIP code.")
    return text


def street_words(raw: str | None) -> list[str]:
    """'231 E. 149th St' -> ['EAST', '149', 'STREET'] (house number dropped)."""
    text = re.sub(r"[^A-Za-z0-9' \-]", " ", (raw or "")).upper()
    words = text.split()
    if words and _HOUSE_NUMBER_RE.match(words[0]) and len(words) > 1:
        words = words[1:]
    out = []
    for i, word in enumerate(words):
        word = re.sub(r"^(\d+)(ST|ND|RD|TH)$", r"\1", word)
        word = _SPELLED_ORDINALS.get(word, word)
        if i == 0 and len(words) > 1 and word in _LEADING:
            word = _LEADING[word]  # "St Nicholas Ave" -> SAINT; "Mt Eden" -> MOUNT
        elif word in _DIRECTIONS and (i == 0 or i < len(words) - 1):
            word = _DIRECTIONS[word]
        elif word in _SUFFIXES and i == len(words) - 1 and i > 0:
            word = _SUFFIXES[word]
        out.append(word)
    return out


def clean_street(raw: str | None, what: str = "street") -> str:
    text = re.sub(r"\s+", " ", (raw or "")).strip()
    if not text:
        raise InvalidArea(f"Enter a {what} name.")
    if len(text) > MAX_STREET_LENGTH:
        raise InvalidArea(f"That {what} name is too long.")
    if not street_words(text):
        raise InvalidArea(f"Enter a {what} name.")
    return text


def display_street(city_spelling: str) -> str:
    """'EAST 149 STREET' -> 'East 149 Street'; keeps "O'Brien"-style names readable."""
    words = []
    for word in (city_spelling or "").split():
        words.append(word if word[:1].isdigit() else word.capitalize())
    return " ".join(words)


def _matches(typed: list[str], candidate: str) -> bool:
    """Does the city's street name fit what was typed?

    Every distinctive word typed must appear; a direction (East/West) or a
    street type (Street/Avenue) that was typed must match too, so "e 149
    st" never becomes "West 149 Street" or "East 149 Avenue".
    """
    cand = candidate.upper().replace(".", "").split()
    cand_set = set(cand)
    core = [w for w in typed if w not in _STREET_TYPES and w not in _DIRECTION_WORDS]
    if not core:
        return False
    if any(w not in cand_set for w in core):
        return False
    for w in typed:
        if (w in _DIRECTION_WORDS or w in _STREET_TYPES) and w not in cand_set:
            return False
    return True


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

_street_cache = _TTLCache(CACHE_TTL_SECONDS, 512)
_lots_cache = _TTLCache(CACHE_TTL_SECONDS, 256)
_districts_cache = _TTLCache(CACHE_TTL_SECONDS, 256)
_violations_cache = _TTLCache(CACHE_TTL_SECONDS, 256)
_boards_cache = _TTLCache(24 * 3600, 2)
_officials_cache = _TTLCache(CACHE_TTL_SECONDS, 2)


def clear_caches() -> None:
    for cache in (_street_cache, _lots_cache, _districts_cache, _violations_cache, _boards_cache, _officials_cache):
        cache.clear()


def resolve_street(raw: str, borough: str) -> str:
    """The city's spelling of a street in a borough. Raises StreetNotFound."""
    typed = street_words(raw)
    key = f"{borough}|{' '.join(typed)}"
    cached = _street_cache.get(key)
    if cached is not None:
        if cached is False:
            raise StreetNotFound(raw)
        return cached
    geo_borough = BOROUGHS[borough][2]
    payload = _get_json(building_service.GEOSEARCH_URL, {"text": f"{' '.join(typed)}, {geo_borough}", "size": 10})
    features = (payload or {}).get("features") if isinstance(payload, dict) else None
    if not isinstance(features, list):
        raise LookupUnavailable("GeoSearch returned an unexpected shape.")
    votes: Counter = Counter()
    for feature in features:
        props = (feature or {}).get("properties") or {}
        street = str(props.get("street") or "").strip().upper()
        if not street or str(props.get("borough") or "") != geo_borough:
            continue
        if _SAFE_STREET_RE.match(street) and _matches(typed, street):
            votes[street] += 1
    if not votes:
        _street_cache.put(key, False)
        raise StreetNotFound(raw)
    # An exact match beats a more common near-match ("Broadway" is not
    # "East Broadway", even if GeoSearch returned more of the latter).
    exact = [s for s in votes if s.split() == typed]
    street = exact[0] if exact else votes.most_common(1)[0][0]
    _street_cache.put(key, street)
    return street


def _soql_text(value: str) -> str:
    return value.replace("'", "''")


def fetch_lots(street: str, borough: str) -> tuple[list[dict], bool]:
    """Every numbered tax lot on the street. (lots, truncated)."""
    key = f"{borough}|{street}"
    cached = _lots_cache.get(key)
    if cached is not None:
        return cached
    code = BOROUGHS[borough][0]
    rows = _get_json(PLUTO_URL, {
        "$select": "bbl,address,zipcode,council,cd,policeprct,schooldist,landuse,unitsres,zonedist1,latitude,longitude",
        "$where": f"borough='{code}' AND address like '% {_soql_text(street)}'",
        "$limit": str(MAX_LOTS),
    })
    if not isinstance(rows, list):
        raise LookupUnavailable("Property data returned an unexpected shape.")
    lots = []
    suffix = " " + street
    for row in rows:
        address = str(row.get("address") or "").strip().upper()
        if not address.endswith(suffix):
            continue
        number = address[: -len(suffix)].strip()
        if not _HOUSE_NUMBER_RE.match(number):
            continue  # "WEST ECHO PLACE" is a different street; unnumbered lots have no door
        lots.append(row)
    result = (lots, len(rows) >= MAX_LOTS)
    _lots_cache.put(key, result)
    return result


def _point(lot: dict) -> tuple[float, float] | None:
    try:
        lat, lon = float(lot.get("latitude")), float(lot.get("longitude"))
    except (TypeError, ValueError):
        return None
    if not (40.4 < lat < 41.0 and -74.3 < lon < -73.6):
        return None
    return lat, lon


def _meters(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat = math.radians((a[0] + b[0]) / 2)
    dy = (a[0] - b[0]) * 111_320
    dx = (a[1] - b[1]) * 111_320 * math.cos(lat)
    return math.hypot(dx, dy)


def near_cross_street(lots: list[dict], cross_lots: list[dict], meters: float = CROSS_STREET_METERS) -> list[dict]:
    cross_points = [p for p in (_point(l) for l in cross_lots) if p]
    if not cross_points:
        return []
    kept = []
    for lot in lots:
        p = _point(lot)
        if p and any(_meters(p, q) <= meters for q in cross_points):
            kept.append(lot)
    return kept


def _int_values(lots: list[dict], key: str) -> list[int]:
    out = Counter()
    for lot in lots:
        try:
            out[int(str(lot.get(key)).strip())] += 1
        except (TypeError, ValueError):
            continue
    return [k for k, _ in out.most_common()]


def _str_values(lots: list[dict], key: str) -> list[str]:
    out = Counter(str(lot.get(key)).strip() for lot in lots if str(lot.get(key) or "").strip() not in ("", "None"))
    return [k for k, _ in out.most_common()]


def sample_points(lots: list[dict], n: int = DISTRICT_SAMPLE_POINTS) -> list[tuple[float, float]]:
    points = [p for p in (_point(l) for l in lots) if p]
    if len(points) <= n:
        return points
    step = len(points) / n
    return [points[int(i * step)] for i in range(n)]


def districts_at(points: list[tuple[float, float]]) -> dict[str, list[int]]:
    """Assembly / State Senate / Congress districts touching these points.
    Any layer that fails is left empty rather than failing the page."""
    out: dict[str, list[int]] = {name: [] for name in DISTRICT_LAYERS}
    if not points:
        return out
    key = ";".join(f"{lat:.5f},{lon:.5f}" for lat, lon in points)
    cached = _districts_cache.get(key)
    if cached is not None:
        return cached
    geometry = '{"points":[' + ",".join(f"[{lon:.6f},{lat:.6f}]" for lat, lon in points) + '],"spatialReference":{"wkid":4326}}'
    for name, (url, field_name) in DISTRICT_LAYERS.items():
        try:
            payload = _get_json(url, {
                "geometry": geometry, "geometryType": "esriGeometryMultipoint", "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects", "outFields": field_name,
                "returnGeometry": "false", "f": "json",
            })
            values = Counter()
            for feature in (payload or {}).get("features") or []:
                value = ((feature or {}).get("attributes") or {}).get(field_name)
                if isinstance(value, (int, float)) and 0 < int(value) < 300:
                    values[int(value)] += 1
            out[name] = sorted(values)
        except LookupUnavailable:
            logger.warning("District map %s unavailable.", name)
    _districts_cache.put(key, out)
    return out


def community_boards() -> dict[str, dict]:
    """Community district code ('205') -> the board's public office details."""
    cached = _boards_cache.get("all")
    if cached is not None:
        return cached
    try:
        rows = _get_json(COMMUNITY_BOARDS_URL, {"$limit": "100"})
    except LookupUnavailable:
        logger.warning("Community board directory unavailable.")
        return {}
    boards = {}
    for row in rows if isinstance(rows, list) else []:
        code = str(row.get("community_board_1") or "").strip()
        if not re.fullmatch(r"[1-5]\d{2}", code):
            continue
        website = row.get("cb_website")
        url = website.get("url") if isinstance(website, dict) else website
        boards[code] = {
            "code": code,
            "name": f"{row.get('borough') or ''} {row.get('community_board') or ''}".strip(),
            "neighborhoods": str(row.get("neighborhoods") or "").strip(),
            "address": re.sub(r"\s+", " ", re.sub(r",(?=\S)", ", ", str(row.get("cb_office_address") or ""))).strip(),
            "phone": str(row.get("cb_office_phone") or "").strip(),
            "email": str(row.get("cb_office_email") or "").strip(),
            "website": url if isinstance(url, str) and url.startswith(("http://", "https://")) else None,
            "meeting": str(row.get("cb_board_meeting") or "").strip(),
        }
    _boards_cache.put("all", boards)
    return boards


def official_url(office: str, district: int) -> str:
    if office == "council":
        return f"https://council.nyc.gov/district-{district}/"
    if office == "assembly":
        return f"https://nyassembly.gov/mem/?ad={district:03d}"
    if office == "state_senate":
        return f"https://www.nysenate.gov/district/{district}"
    return HOUSE_FIND_REP_URL


def load_officials(client) -> tuple[dict[tuple[str, int], str], str | None]:
    """(office, district) -> name, plus the refresh date. Empty on any trouble."""
    cached = _officials_cache.get("all")
    if cached is not None:
        return cached
    names: dict[tuple[str, int], str] = {}
    as_of = None
    try:
        if client is None:
            raise RuntimeError("no client")
        response = client.table("elected_officials").select("office,district,name,refreshed_at").limit(1000).execute()
        for row in response.data or []:
            names[(str(row["office"]), int(row["district"]))] = str(row["name"])
            stamp = str(row.get("refreshed_at") or "")[:10]
            as_of = max(as_of or stamp, stamp)
    except Exception:  # noqa: BLE001 -- names are a bonus; district numbers still show
        logger.warning("Elected officials unavailable.", exc_info=True)
        return {}, None
    _officials_cache.put("all", (names, as_of))
    return names, as_of


def open_violations_by_building(street: str, borough: str) -> dict[str, dict] | None:
    """bbl -> {"counts": {class: open}, "house", "zip", "point"} for the
    street. None if unavailable.

    HPD files a corner building under whichever street it uses -- 175
    Echo Place is on the Echo Place list here even though its tax lot is
    addressed on another street -- so buildings found only here are added
    to the street's list too (see lookup).
    """
    key = f"{borough}|{street}"
    cached = _violations_cache.get(key)
    if cached is not None:
        return cached
    try:
        rows = _get_json(building_service.VIOLATIONS_URL, {
            "$select": "bbl,housenumber,zip,latitude,longitude,class,count(*) as n",
            "$where": f"boroid='{BOROUGHS[borough][1]}' AND streetname='{_soql_text(street)}' AND violationstatus='Open'",
            "$group": "bbl,housenumber,zip,latitude,longitude,class",
            "$limit": "10000",
        })
    except LookupUnavailable:
        logger.warning("Violations by street unavailable.")
        return None
    buildings: dict[str, dict] = {}
    for row in rows if isinstance(rows, list) else []:
        bbl = str(row.get("bbl") or "").strip()
        cls = str(row.get("class") or "").strip().upper()
        try:
            n = int(row.get("n") or 0)
        except (TypeError, ValueError):
            continue
        if not building_service.is_valid_bbl(bbl) or not cls:
            continue
        entry = buildings.setdefault(bbl, {"counts": {}, "house": "", "zip": "", "point": None})
        entry["counts"][cls] = entry["counts"].get(cls, 0) + n
        house = str(row.get("housenumber") or "").strip().upper()
        if not entry["house"] and _HOUSE_NUMBER_RE.match(house):
            entry["house"] = house
        entry["zip"] = entry["zip"] or str(row.get("zip") or "").strip()
        entry["point"] = entry["point"] or _point(row)
    _violations_cache.put(key, buildings)
    return buildings


def _house_sort_key(address: str):
    number = address.split(" ", 1)[0]
    parts = re.findall(r"\d+", number)
    return tuple(int(p) for p in parts) + (number,)


def lookup(street: str, borough: str, zip_code: str | None = None, cross_street: str | None = None,
           client=None) -> AreaReport:
    """Everything the area page shows. Raises InvalidArea, StreetNotFound,
    CrossStreetNotFound or LookupUnavailable."""
    borough = borough_from(borough)
    typed = clean_street(street)
    zip_code = clean_zip(zip_code)
    cross_typed = clean_street(cross_street, "cross street") if (cross_street or "").strip() else None

    city_street = resolve_street(typed, borough)
    lots, truncated = fetch_lots(city_street, borough)
    if not lots:
        raise StreetNotFound(typed)

    city_cross = None
    cross_lots_used = None
    if zip_code:
        lots = [l for l in lots if str(l.get("zipcode") or "").strip() == zip_code]
        if not lots:
            raise InvalidArea(f"No buildings on {display_street(city_street)} are in ZIP code {zip_code}.")
    if cross_typed:
        try:
            city_cross = resolve_street(cross_typed, borough)
        except StreetNotFound:
            raise CrossStreetNotFound(cross_typed) from None
        cross_lots, _ = fetch_lots(city_cross, borough)
        near = near_cross_street(lots, cross_lots)
        if not near:
            raise CrossStreetNotFound(cross_typed)
        lots = near
        cross_lots_used = cross_lots

    land = Counter(str(l.get("landuse") or "").strip().lstrip("0") for l in lots)
    land_use = [(LAND_USE[k], n) for k, n in land.most_common() if k in LAND_USE]
    homes = 0
    for lot in lots:
        try:
            homes += int(float(lot.get("unitsres") or 0))
        except (TypeError, ValueError):
            pass
    zoning_codes = _str_values(lots, "zonedist1")
    zoning = ZONING_KINDS.get(zoning_codes[0][:1].upper()) if zoning_codes else None

    districts = districts_at(sample_points(lots))
    council = _int_values(lots, "council")
    names, as_of = load_officials(client)
    officials = []
    for office, numbers in (("council", council), ("assembly", districts["assembly"]), ("state_senate", districts["state_senate"])):
        for d in numbers:
            officials.append(Official(office, d, names.get((office, d)), official_url(office, d)))
    for d in districts["congress"]:
        officials.append(Official("congress", d, None, HOUSE_FIND_REP_URL))

    cds = _str_values(lots, "cd")
    boards = community_boards()
    board_rows = [boards[c] for c in cds if c in boards]

    hpd = open_violations_by_building(city_street, borough)
    buildings = []
    listed = set()
    for lot in lots:
        if str(lot.get("landuse") or "").strip().lstrip("0") not in RESIDENTIAL_LAND_USE:
            continue
        bbl = str(lot.get("bbl") or "").split(".")[0]
        try:
            units = int(float(lot.get("unitsres") or 0))
        except (TypeError, ValueError):
            units = 0
        listed.add(bbl)
        buildings.append(Building(
            bbl=bbl,
            address=display_street(str(lot.get("address") or "")),
            homes=units,
            open_counts=dict(((hpd or {}).get(bbl) or {}).get("counts", {})),
        ))
    # Buildings HPD lists on this street whose tax lot is addressed on
    # another one (corner buildings), kept to the same ZIP / corner.
    for bbl, entry in (hpd or {}).items():
        if bbl in listed or not entry["house"]:
            continue
        if zip_code and entry["zip"] != zip_code:
            continue
        if cross_lots_used is not None and not (entry["point"] and near_cross_street([{"latitude": entry["point"][0], "longitude": entry["point"][1]}], cross_lots_used)):
            continue
        buildings.append(Building(bbl=bbl, address=display_street(f"{entry['house']} {city_street}"), homes=0,
                                  open_counts=dict(entry["counts"])))
    buildings.sort(key=lambda b: _house_sort_key(b.address))

    return AreaReport(
        street=city_street,
        borough=borough,
        zip_code=zip_code,
        cross_street=city_cross,
        lot_count=len(lots),
        homes=homes,
        land_use=land_use,
        zoning=zoning,
        zoning_codes=zoning_codes[:3],
        council=council,
        community_districts=cds,
        zip_codes=_str_values(lots, "zipcode"),
        precincts=_str_values(lots, "policeprct"),
        school_districts=_str_values(lots, "schooldist"),
        assembly=districts["assembly"],
        state_senate=districts["state_senate"],
        congress=districts["congress"],
        officials=officials,
        community_boards=board_rows,
        buildings=buildings[:MAX_BUILDINGS_SHOWN],
        buildings_total=len(buildings),
        violations_available=hpd is not None,
        officials_as_of=as_of,
        truncated=truncated,
    )


OFFICE_TITLES = {
    "council": "Council Member",
    "assembly": "Assembly Member",
    "state_senate": "State Senator",
    "congress": "U.S. Representative",
}
