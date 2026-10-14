"""'Did you mean' for the street and address searches.

WHY THIS EXISTS (owner, 2026-10-11)
A misspelled street was a dead end ("We couldn't find that street"), and
worse, a misspelled ADDRESS could silently open a different building:
the city's geocoder answers "231 Ecco Place, Bronx" with 231 BROWN PLACE,
and the building page showed Brown Place's violations as if they were the
tenant's. Measured live on 2026-10-11, the geocoder labels every result
"fallback", including exact ones, so its own confidence field can't tell
the two apart. The street and house number are compared here instead.

WHAT IT DOES
  * same_street / same_number decide whether a geocoder result is the
    address that was typed. building_service.geocode uses them and never
    returns a building on a different street.
  * for_street / for_address offer up to MAX_SUGGESTIONS corrections when
    a search finds nothing.

HOW SUGGESTIONS ARE CHOSEN (deterministic; no model call)
  1. The city's own list of street names for the borough (HPD's buildings
     dataset, one fetch per borough, kept for a day).
  2. Fuzzy-compared with what was typed, on the distinctive words only:
     a street type or direction both sides share is set aside first, or
     "Asdfgh Street" would look like "Mead Street". A typed direction or
     street type, and any street number, must match exactly.
  3. Each survivor is confirmed before it is shown (the street resolves,
     or the corrected address geocodes exactly), so a tap never lands on
     another dead end. The list holds misspellings of its own ("GR
     CONCOURSE"); confirmation is what drops them.

Nothing here ever redirects: suggestions are links the tenant chooses.
Any failure means no suggestions; the not-found page is unchanged.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher

import area_service
import building_service
from building_service import LookupUnavailable, _TTLCache

logger = logging.getLogger(__name__)

STREET_LIST_URL = "https://data.cityofnewyork.us/resource/kj4p-ruqc.json"
MAX_SUGGESTIONS = 3
MIN_SIMILARITY = 0.75
# Short names differ by one letter too easily ("Mead" / "Read").
MIN_SIMILARITY_SHORT = 0.85
SHORT_NAME_LENGTH = 4
# Each confirmation is one request to the geocoder; a not-found page must
# not turn into a dozen of them.
MAX_CONFIRMATIONS = 5

_lists_cache = _TTLCache(24 * 3600, 8)
# After the street list fails to load, don't ask again for a while: a
# not-found page must not wait on a service that is down, five times over.
LIST_RETRY_SECONDS = 120
_list_failed_at = [0.0]

_TYPE_OR_DIRECTION = area_service._STREET_TYPES | area_service._DIRECTION_WORDS
_ALIASES = {
    "BRONX": "Bronx", "BX": "Bronx", "BROOKLYN": "Brooklyn", "BK": "Brooklyn", "BKLYN": "Brooklyn",
    "MANHATTAN": "Manhattan", "QUEENS": "Queens", "QNS": "Queens", "SI": "Staten Island",
}
_TWO_WORD_ALIASES = {("STATEN", "ISLAND"): "Staten Island", ("THE", "BRONX"): "Bronx", ("NEW", "YORK"): None}
_FILLER = {"NY", "NYC", "USA"}
_ZIP_BOROUGHS = {"100": "Manhattan", "101": "Manhattan", "102": "Manhattan", "103": "Staten Island",
                 "104": "Bronx", "112": "Brooklyn", "110": "Queens", "111": "Queens", "113": "Queens",
                 "114": "Queens", "116": "Queens"}
_ZIP_RE = re.compile(r"^\d{5}$")


@dataclass(frozen=True)
class TypedAddress:
    number: str | None          # "231", "31-10", "12A"; None if the text doesn't start with one
    words: tuple[str, ...]      # the street as street_words() spells it
    borough: str | None         # a key of area_service.BOROUGHS, if the text named one


@dataclass(frozen=True)
class Suggestion:
    street: str                  # the city's spelling, e.g. "ECHO PLACE"
    borough: str                 # a key of area_service.BOROUGHS
    house_number: str | None = None
    other_borough: bool = False  # the same name, in a borough other than the one searched

    @property
    def street_label(self) -> str:
        return area_service.display_street(self.street)

    @property
    def address(self) -> str | None:
        """What the building search takes, e.g. '231 Echo Place, Bronx'."""
        return f"{self.house_number} {self.street_label}, {self.borough}" if self.house_number else None

    @property
    def label(self) -> str:
        if self.house_number:
            return self.address
        return f"{self.street_label}, {self.borough}"


def clear_caches() -> None:
    _lists_cache.clear()
    _list_failed_at[0] = 0.0


# ---------------------------------------------------------------------------
# Reading what was typed, and comparing it with a geocoder result
# ---------------------------------------------------------------------------


def _borough_of(tokens: list[str]) -> tuple[str | None, int]:
    """(borough, how many trailing tokens named it or were filler)."""
    borough, used = None, 0
    while len(tokens) - used > 0:
        rest = tokens[: len(tokens) - used]
        last = rest[-1]
        pair = tuple(rest[-2:]) if len(rest) >= 2 else None
        if pair in _TWO_WORD_ALIASES:
            borough = borough or _TWO_WORD_ALIASES[pair]
            used += 2
        elif last in _ALIASES:
            borough = borough or _ALIASES[last]
            used += 1
        elif _ZIP_RE.match(last) and last[:3] in _ZIP_BOROUGHS:
            borough = borough or _ZIP_BOROUGHS[last[:3]]
            used += 1
        elif last in _FILLER:
            used += 1
        else:
            break
    return borough, used


def parse_address(text: str | None) -> TypedAddress:
    """'231 echo pl, bx' -> ('231', ('ECHO', 'PLACE'), 'Bronx').

    Only what follows the street is read as a borough, so "100 Queens
    Blvd" and "5 Bronx Park East" keep their street names.
    """
    segments = [s for s in (text or "").split(",")]
    tokens = re.sub(r"[^A-Za-z0-9' \-]", " ", segments[0]).upper().split()
    borough = None
    for segment in segments[1:]:
        found, _ = _borough_of(re.sub(r"[^A-Za-z0-9 ]", " ", segment).upper().split())
        borough = borough or found
    if not tokens or not area_service._HOUSE_NUMBER_RE.match(tokens[0]):
        return TypedAddress(None, (), borough)
    if len(tokens) > 2:
        found, used = _borough_of(tokens[2:])
        if used:
            tokens = tokens[: len(tokens) - used]
            borough = borough or found
    words = tuple(area_service.street_words(" ".join(tokens))) if len(tokens) > 1 else ()
    return TypedAddress(tokens[0], words, borough)


@dataclass(frozen=True)
class StreetSearch:
    """What a whole address typed into the street box comes down to."""
    street: str                  # the street part only, e.g. "ECHO PLACE"; "" if nothing is left
    borough: str | None          # named in the text or implied by its ZIP code
    zip_code: str | None         # a 5-digit NYC ZIP code found in the text
    number: str | None           # the house number, if the text started with one


_UNIT_WORDS = {"APT", "APARTMENT", "UNIT", "FL", "FLOOR", "STE", "SUITE", "RM", "ROOM", "PH"}


def _zip_in(tokens) -> str | None:
    for token in tokens:
        if _ZIP_RE.match(token) and token[:3] in _ZIP_BOROUGHS:
            return token
    return None


def read_street_search(raw: str | None) -> StreetSearch:
    """'231 Echo Pl Apt 3B, Bronx, NY 10457' -> street 'ECHO PL', Bronx, 10457, number 231.

    WHY (owner, 2026-10-14): tenants type their whole address into the
    front page's street box, and "231 Echo Place, Bronx, NY 10457" came
    back as "We couldn't find that street" (the borough, state and ZIP
    were read as part of the street name), "Choose a borough" when the
    menu was left empty, or not found with an apartment in it. What the
    street box needs is the street; the rest picks the borough and ZIP.

    Only the first comma-separated part can hold the street. Within it an
    apartment ("Apt 3B", "#3B", "Unit 4") and anything after it is dropped,
    then a trailing borough, "NY", "USA" or ZIP (as in parse_address), and
    then a neighbourhood after the street type ("82nd St Jackson Heights"
    -> "82nd St"). Later parts only supply a borough or ZIP; a
    neighbourhood there ("Jackson Heights") is ignored.
    """
    segments = (raw or "").split(",")
    tokens = re.sub(r"[^A-Za-z0-9#' \-]", " ", segments[0]).upper().split()
    after_unit = []
    for i, token in enumerate(tokens):
        if i > 0 and (token in _UNIT_WORDS or token.startswith("#")):
            # "Apt 3B Bronx NY 10451": the unit goes, what follows it can still name the place.
            after_unit = tokens[i + (1 if token.startswith("#") and len(token) > 1 else 2):]
            tokens = tokens[:i]
            break
    tokens = [t for t in tokens if not t.startswith("#")]
    number = None
    if len(tokens) > 1 and area_service._HOUSE_NUMBER_RE.match(tokens[0]):
        number, tokens = tokens[0], tokens[1:]

    zip_code = _zip_in(tokens[1:])
    borough, used = _borough_of(tokens[1:])
    if used:
        tokens = tokens[: len(tokens) - used]
    for words in [after_unit] + [re.sub(r"[^A-Za-z0-9 ]", " ", seg).upper().split() for seg in segments[1:]]:
        zip_code = zip_code or _zip_in(words)
        found, _ = _borough_of(words)
        borough = borough or found

    # A neighbourhood typed after the street type, with no comma.
    for i, token in enumerate(tokens):
        kind = area_service._SUFFIXES.get(token, token)
        rest = tokens[i + 1:]
        if i > 0 and kind in area_service._STREET_TYPES and rest and not all(
            area_service._DIRECTIONS.get(w, w) in area_service._DIRECTION_WORDS for w in rest
        ):
            tokens = tokens[: i + 1]
            break
    return StreetSearch(" ".join(tokens), borough, zip_code, number)


def feature_address(props: dict) -> tuple[str, str]:
    """(house number, street) of a geocoder result, in capitals; '' if unknown."""
    number = str(props.get("housenumber") or "").strip().upper()
    street = str(props.get("street") or "").strip().upper()
    if number and street:
        return number, street
    first = str(props.get("label") or props.get("name") or "").split(",")[0].upper().split()
    if len(first) >= 2 and area_service._HOUSE_NUMBER_RE.match(first[0]):
        return number or first[0], street or " ".join(first[1:])
    return number, street


def _number_key(number: str | None) -> str:
    return re.sub(r"[\s\-]", "", (number or "")).upper()


def same_number(typed: str | None, found: str | None) -> bool:
    """'31-10' is '3110'; '12A' is '12a' but not '12'."""
    return bool(_number_key(typed)) and _number_key(typed) == _number_key(found)


def _city_words(city_street: str) -> list[str]:
    """'WEST FOURTH STREET' -> ['WEST', '4', 'STREET']; '5TH AVENUE' -> ['5', 'AVENUE']."""
    out = []
    for word in city_street.upper().replace(".", "").split():
        word = re.sub(r"^(\d+)(ST|ND|RD|TH)$", r"\1", word)
        out.append(area_service._SPELLED_ORDINALS.get(word, word))
    return out


def same_street(typed_words, city_street: str) -> bool:
    """Is the geocoder's street the one that was typed?

    Either the typed words fit the street (area_service._matches), or the
    street's words are all among the typed ones. The second half is for
    text with no commas, where a neighbourhood trails the street ("100
    Broadway Astoria"): BROADWAY is still what was typed.
    """
    words = list(typed_words)
    if not words or not city_street:
        return False
    # The geocoder echoes the spelling it was given ("400 First Avenue"
    # comes back as FIRST AVENUE, "350 5th Ave" as 5 AVENUE), while typed
    # words are already normalized to digits by area_service.street_words.
    # Normalize the city's words the same way before comparing.
    city_street = " ".join(_city_words(city_street))
    if area_service._matches(words, city_street):
        return True
    typed_set = set(words)
    for word in words:
        for table in (area_service._SUFFIXES, area_service._DIRECTIONS, area_service._LEADING):
            if word in table:
                typed_set.add(table[word])
    city = city_street.upper().replace(".", "").split()
    return any(w not in _TYPE_OR_DIRECTION for w in city) and all(w in typed_set for w in city)


# ---------------------------------------------------------------------------
# The city's street names, and how close a name is to what was typed
# ---------------------------------------------------------------------------


def borough_streets(borough: str, get_json=None) -> tuple[str, ...]:
    """Every street name the city lists in a borough, normalized and deduped.

    Raises LookupUnavailable if the list can't be fetched.
    """
    cached = _lists_cache.get(borough)
    if cached is not None:
        return cached
    if _list_failed_at[0] and time.monotonic() - _list_failed_at[0] < LIST_RETRY_SECONDS:
        raise LookupUnavailable("Street list failed recently; not retrying yet.")
    boroid = area_service.BOROUGHS[borough][1]
    try:
        rows = (get_json or area_service._get_json)(STREET_LIST_URL, {
            "$select": "streetname", "$where": f"boroid='{boroid}'", "$group": "streetname", "$limit": "50000",
        })
        if not isinstance(rows, list):
            raise LookupUnavailable("Street list returned an unexpected shape.")
    except LookupUnavailable:
        _list_failed_at[0] = time.monotonic()
        raise
    names = set()
    for row in rows:
        raw = str((row or {}).get("streetname") or "").strip().upper() if isinstance(row, dict) else ""
        if not raw or not area_service._SAFE_STREET_RE.match(raw):
            continue
        # The leading "0" stands in for a house number, so a numbered
        # street ("37 AVENUE") isn't mistaken for one.
        words = area_service.street_words("0 " + raw)
        if words:
            names.add(" ".join(words))
    result = tuple(sorted(names))
    _lists_cache.put(borough, result)
    return result


def similarity(typed_words, name: str) -> float:
    """0..1: how close a city street name is to the typed words. 0 if ruled out."""
    typed = list(typed_words)
    cand = name.split()
    cand_set = set(cand)
    if any(w in _TYPE_OR_DIRECTION and w not in cand_set for w in typed):
        return 0.0  # a typed direction or street type is never corrected
    if [w for w in typed if w[:1].isdigit()] != [w for w in cand if w[:1].isdigit()]:
        return 0.0  # East 148 Street is not a spelling of East 149 Street
    shared = _TYPE_OR_DIRECTION & set(typed) & cand_set
    left = " ".join(w for w in typed if w not in shared)
    right = " ".join(w for w in cand if w not in shared)
    if not left or not right:
        return 0.0
    score = SequenceMatcher(None, left, right).ratio()
    floor = MIN_SIMILARITY_SHORT if min(len(left), len(right)) < SHORT_NAME_LENGTH else MIN_SIMILARITY
    return score if score >= floor else 0.0


def closest(typed_words, names, also_returned=()) -> list[str]:
    """City names worth confirming, best first. `also_returned` are streets
    the geocoder itself offered; among equals they go first."""
    boost = {" ".join(area_service.street_words("0 " + s)) for s in also_returned}
    scored = []
    for name in names:
        score = similarity(typed_words, name)
        if score:
            scored.append((-(score + (0.05 if name in boost else 0.0)), name))
    return [name for _, name in sorted(scored)]


# ---------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------


def _confirmed_street(name: str, borough: str, get_json=None) -> str | None:
    """The city's spelling if the area page would open for this street, else None.

    The geocoder knows alias spellings ("GRANDCONCOURSE") that no tax lot
    uses; the area page would be a dead end for those, so a street also
    has to have lots.
    """
    try:
        street = area_service.resolve_street(name, borough, get_json=get_json)
    except area_service.StreetNotFound:
        return None
    lots, _ = area_service.fetch_lots(street, borough, get_json=get_json)
    return street if lots else None


def for_street(raw: str, borough: str, also_returned=()) -> list[Suggestion]:
    """Up to MAX_SUGGESTIONS streets for a street search that found nothing."""
    try:
        typed = area_service.street_words(raw)
        if not typed:
            return []
        found: list[Suggestion] = []
        seen = set()

        def add(street, where, other):
            if (street, where) not in seen:
                seen.add((street, where))
                found.append(Suggestion(street, where, other_borough=other))

        names = borough_streets(borough)
        # The same name in another borough is the likeliest slip of all.
        for other in area_service.BOROUGHS:
            if other == borough:
                continue
            if any(area_service._matches(typed, name) for name in borough_streets(other)):
                street = _confirmed_street(raw, other)
                if street:
                    add(street, other, True)
        tries = 0
        for name in closest(typed, names, also_returned):
            if len(found) >= MAX_SUGGESTIONS or tries >= MAX_CONFIRMATIONS:
                break
            tries += 1
            street = _confirmed_street(name, borough)
            if street:
                add(street, borough, False)
        return found[:MAX_SUGGESTIONS]
    except LookupUnavailable:
        logger.warning("Street suggestions unavailable.", exc_info=True)
        return []
    except Exception:
        logger.exception("Street suggestions failed.")
        return []


def for_address(typed: TypedAddress | None, returned=()) -> list[Suggestion]:
    """Up to MAX_SUGGESTIONS corrections for a building search that found nothing.

    `returned` is (street, borough) for what the geocoder offered instead.
    A corrected address is shown only if it geocodes to exactly that
    number on that street. Failing that, the closest street is offered on
    its own, so the tenant can pick their building from its list.
    """
    try:
        if typed is None or not typed.number or not typed.words:
            return []
        get_json = building_service._get_json
        # With no borough typed, the ones the geocoder answered with are
        # the only clue to where the tenant means.
        boroughs = [typed.borough] if typed.borough else []
        if not boroughs:
            for _, where in returned:
                if where in area_service.BOROUGHS and where not in boroughs:
                    boroughs.append(where)
        found: list[Suggestion] = []
        tries = 0
        for borough in boroughs[:2]:
            names = closest(typed.words, borough_streets(borough, get_json), [s for s, b in returned if b == borough])
            before = len(found)
            for name in names:
                if len(found) >= MAX_SUGGESTIONS or tries >= MAX_CONFIRMATIONS:
                    break
                tries += 1
                street = area_service.display_street(name)
                try:
                    match = building_service.geocode(f"{typed.number} {street}, {borough}")
                except building_service.AddressNotFound:
                    continue
                if match.requested is None and match.street and same_number(typed.number, match.house_number):
                    suggestion = Suggestion(match.street, borough, house_number=match.house_number)
                    if suggestion not in found:
                        found.append(suggestion)
            if len(found) == before and names and len(found) < MAX_SUGGESTIONS:
                street = _confirmed_street(names[0], borough, get_json)
                if street:
                    found.append(Suggestion(street, borough))
        return found[:MAX_SUGGESTIONS]
    except LookupUnavailable:
        logger.warning("Address suggestions unavailable.", exc_info=True)
        return []
    except Exception:
        logger.exception("Address suggestions failed.")
        return []
