# Building lookup (`/building`)

`templates/building.html` + `templates/_violation.html`, backed by
`building_service.py`.

## Why it exists

It's the first thing on the site a general chatbot can't do. A chatbot can
explain tenant law. It can't tell someone that *their* building has three
open Class C violations, one of them for heat, logged in February and still
open. That needs live public data.

## Why it's public

It's the front door. The site used to ask for an email address before
showing anything useful; this shows real value first. The signup and login
pages both link to it, and so does the chat empty state.

The top disclaimer banner is **not** on this page, on purpose. The banner
tells people to message the chatbot, which a logged-out visitor can't do.
Same rule as the other public pages. The footer disclaimer is there.

## Data flow

1. **GeoSearch** (NYC Planning, free, no key) turns "231 echo pl bx" into
   `231 ECHO PLACE, Bronx` plus a **BBL** (borough-block-lot).
2. **HPD violations** (NYC Open Data `wvxf-dwi5`, free, no key, updated
   daily) queried **by BBL**.

Query by BBL, never by street name. HPD writes "ECHO PLACE", tenants write
"Echo Pl". A string match between the two finds nothing, and it finds
nothing *silently*, which would tell a tenant in a dangerous building that
it was clean.

The BBL is interpolated into a SoQL `$where`. `is_valid_bbl()` (ten digits,
first one 1-5) is the only thing that makes that safe, and the query
functions refuse to run without it. A geocode result carrying a malformed
BBL is skipped, not queried. There are tests for exactly this.

Three calls per building: the open list (capped at 1000), an aggregate
count of open by class, and an aggregate for the last two years. **The
counts at the top come from the aggregates, not the capped list**, so they
stay exact for the worst buildings in the city. Cached for an hour per
building; the dataset only updates daily anyway.

`NYC_OPEN_DATA_APP_TOKEN` is optional. Without it, requests share a
throttled public pool. It's sent only to `data.cityofnewyork.us`, never
to GeoSearch (tested).

## Honesty rules the page enforces

- **The matched address is always shown**, next to "Not your building?".
  A wrong geocode is visible instead of silent.
- **The "as of" date is the city's**, from the dataset's `rowsUpdatedAt`.
  If that can't be fetched, the page says the date is unavailable. It
  never falls back to today's date, because that would tell a tenant the
  data is fresher than we know it is. Tested.
- **"Certified" is shown separately from "open".** A certification is the
  landlord telling HPD the problem is fixed. HPD's own guidance says
  tenants are notified and can challenge it, which triggers a
  re-inspection. The page says that, and links to HPD.
- **Zero violations isn't "nothing wrong".** The empty state says none are
  currently recorded and points to 311.
- **Correction deadlines are only stated where HPD states them plainly.**
  Class A 90 days, Class B 30 days, most Class C 24 hours with named
  21-day exceptions. Anything less clear-cut links out instead of
  paraphrasing a legal deadline.

## Status labels

HPD's `NOV SENT OUT` used to be title-cased to "Nov Sent Out". It sat next
to an inspection date and read as **November**. NOV means Notice of
Violation. `readable_status()` maps the known statuses to plain English and
spells out NOV in any status it doesn't know, so a new HPD status can't
bring the bug back.

## Handing off to the chat

"Talk this through" doesn't send hidden context. It builds a plain-text
first message (`chat_prompt()`) that the tenant **sees as their own**:
their address, the counts, the as-of date, up to three violations with the
laws they cite. The tenant knows exactly what the assistant was told and
can correct it if the match was wrong. It's capped well under the chat's
message limit.

It rides the existing `pendingChatPrompt` sessionStorage mechanism the
suggestion chips already use:

- **Logged in:** POST to `create_conversation`, same as a chip.
- **Logged out:** the prompt and a title are stored, then the tenant signs
  up or logs in. The chat empty state shows a highlighted "Continue: 231
  Echo Place" chip when a pending prompt is waiting. **It's a click, not an
  auto-POST on page load.** Creating a conversation just because a page
  loaded would be surprising.

### The stale-prompt fix that came with it

Before this, a pending prompt that was stored but never used would
auto-send into the **next unrelated conversation** the tenant opened,
including one started with a plain "+ New chat". The building page made
that far more likely, so "+ New chat" now clears any pending prompt.
Only something that carries a prompt (a chip, the continue chip, the
building CTA) causes an auto-send. Browser-tested both ways.

## `--on-accent`

Found while reviewing this page's dark-mode screenshot, and **fixed
site-wide**, not just here. White text on the dark-mode accent (`#5b9bd5`)
measured **2.96:1**, below WCAG AA's 4.5:1. That was every filled button,
the send button, the avatar and the ST mark on every themed page.

Text on an accent fill now uses `--on-accent`: white in light mode (7.0:1
on `#1c5d8c`), near-black in dark mode (6.05:1 on `#5b9bd5`, and still
above 4.5:1 on the lighter hover shade). A test fails if hardcoded white
comes back on any themed page, and a browser audit measured all 30
accent-filled text elements in both themes at or above 4.5:1.
