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

## A whole address in the street box (2026-10-14)

Tenants type their full address into the street box, so the box reads it
(`street_suggestions.read_street_search`) before searching:

- Only the first comma-separated part can be the street. An apartment
  ("Apt 3B", "#3B", "Unit 4") is dropped; a trailing borough, "NY", "USA"
  or ZIP is read as the place; a neighbourhood after the street type with
  no comma ("82nd St Jackson Heights") is dropped. Later parts only supply
  a borough or ZIP ("Jackson Heights" there is ignored).
- The borough menu wins when it is set; otherwise the borough in the text
  (or implied by its ZIP) is used and shown as selected. Before this, a
  whole address got "Choose a borough" or "We couldn't find that street".
- A ZIP from the text narrows the street, but if no lot on the street has
  that ZIP the whole street is shown instead of an error. A ZIP typed in
  the ZIP box is still checked strictly.
- With a house number, the result links to that building's own record
  ("Your building: 231 Echo Place, Bronx" -> `/building`). The page still
  never asks for a number.

Every NYC Open Data query (street-wide or one building's) uses
`building_service.SLOW_QUERY_TIMEOUT_SECONDS` (15 s) instead of 6 s: they
took up to 5.5 s from a Mac, and on Render both the first search of a long
street and a building page (2026-10-15, 231 Echo Place) sometimes timed out
into "The city's data service didn't respond". The geocoder keeps 6 s.

## Honesty rules

- A street GeoSearch "finds" by fuzzy match is **not found** unless every
  distinctive word typed is in the city's spelling, and any typed
  direction (East/West) or type (Street/Avenue) matches.
- Nothing is guessed when a source is down: districts, names, the board
  and violation counts each degrade to "unavailable" separately; only the
  property records failing fails the page (503).
- Public wording never names where data comes from in the backend
  ("From public City and State records", details behind the "i").
- A street that isn't found is still "not found" (404). Since 2026-10-11
  the page may add up to three **"Did you mean"** links below the message.
  They are suggestions the tenant taps, never a redirect, and never a
  result shown in place of what was typed.

## "Did you mean" (`street_suggestions.py`)

Added 2026-10-11. A misspelled street was a dead end, and the geocoder
does not fix most typos: "Ecco Place, Bronx" returns unrelated streets.

- **Where suggestions come from.** The city's own list of street names for
  the borough (one fetch per borough, kept a day), compared with what was
  typed. No model call.
- **What is compared.** Only the distinctive words. A street type or
  direction both names share is set aside first, otherwise "Asdfgh Street"
  looks like "Mead Street". A typed direction, a typed street type and any
  street number must match exactly: East 148 Street is not a spelling of
  East 149 Street.
- **Wrong borough.** If the typed name is a street in another borough,
  that borough is offered first, marked "A different borough".
- **Every suggestion is confirmed before it is shown**: the street
  resolves and has buildings to list. The city's list contains
  misspellings of its own ("GR CONCOURSE", "GRANDCONCOURSE") and the
  geocoder knows them as aliases, but no tax lot uses them, so the area
  page would be a dead end. Confirmation is what drops them.
- **Bounded.** At most three suggestions and five confirmations per page.
  If the street list can't be fetched, there are no suggestions, the page
  is the plain not-found page, and the list isn't asked for again for two
  minutes.
- **Cost.** The first not-found search after a restart fetches the five
  borough lists (about 3 to 5 seconds, measured 2026-10-11); later ones
  take about a second. Suggestions are worked out inside the search that
  was already counted against the lookup limit; tapping one is a normal
  lookup.
- A ZIP code or cross street the tenant typed is carried into a
  same-borough suggestion's link.

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
