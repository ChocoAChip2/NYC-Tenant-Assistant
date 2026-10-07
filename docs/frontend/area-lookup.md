# Street lookup (`/`, `/area`) and Resources (`/resources`)

## Why the front page asks for a street, not an address

Owner, 2026-10-07: asking for an exact address up front scares people
off. The front page now asks for a **street and borough** (ZIP code and
cross street optional, behind a disclosure). The exact-address lookup
(`/building`, `building.html`) is one link away, and old
`/?address=...` links still go to it.

`area_service.lookup()` does the work; see its docstring for the data
sources. What the page shows:

- **Header card:** the city's spelling of the street, what it's like
  ("Mostly elevator apartment buildings · about 561 homes · zoned
  residential", with the zoning code behind an "i").
- **"This street crosses more than one district"** when the Council,
  community, Assembly or State Senate district isn't single. The ZIP /
  cross-street fields open automatically so the tenant can narrow it.
- **Your representatives:** Council Member, Assembly Member, State
  Senator (names from `elected_officials`, links to each official
  district page), U.S. Representative (district number + the House's
  "find your representative" page; we don't keep federal names).
- **Community board:** office, phone, email, meeting time, website, from
  the City's community board directory.
- **Area facts:** ZIP codes, police precinct, school district, community
  district.
- **Homes on this street:** residential lots plus corner buildings HPD
  files under this street, each with its open violations ("N hazardous"
  = Class C). Tapping one opens `/building` for that address, so the
  tenant never types a house number.

## Honesty rules

- A street GeoSearch "finds" by fuzzy match is **not found** unless every
  distinctive word typed is in the city's spelling, and any typed
  direction (East/West) or type (Street/Avenue) matches.
- Nothing is guessed when a source is down: districts, names, the board
  and violation counts each degrade to "unavailable" separately; only the
  property records failing fails the page (503).
- Public wording never names where data comes from in the backend
  ("From public City and State records", details behind the "i").

## Officials are refreshed after elections, not per page

`tools/officials/refresh.py`, run by `.github/workflows/refresh-officials.yml`
on Dec 15 and Jan 15 (about a month after the November election, after
certification and ranked-choice counts) and on Apr/Jul/Oct 15 for special
elections; run it by hand after a resignation or special election. It
gates on counts and one-person-per-district, writes through the
token-checked `officials_replace()`, and opens an issue listing every
change. A district with no row (a vacancy) shows "See who represents
District N" with the official link.

## Resources tab

`/resources` is read-only: borough picker, the community board (when
`cd=` is given, e.g. from the street page's "Local resources" link and
only if it belongs to the chosen borough), the borough's Housing Court,
free help (`branding.HELP_RESOURCES`, the same list the chat gives) and
where to report problems (`resources.py`). Every number and address was
checked against the agency's own page; `resources.VERIFIED` records when.
The community board section of the page is the start of the area-based
community features the owner has planned (posting is on hold).
