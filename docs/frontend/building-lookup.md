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

### `bbl` alone misses whole buildings

Measured on the live dataset: `bbl` is filled on 99.88% of open
violations, and the gaps are not a lag. They **cluster by building**. At
4601 Henry Hudson Parkway not one violation had a `bbl`, so a `bbl`-only
query showed **0 open violations. There were 138, including 9 Class C.**
HPD's own `boroid`/`block`/`lot` are filled on 100% of rows, so the filter
is `(bbl=X OR (boroid, block, lot) = X's)`. That's strictly additive:
normal buildings return exactly what they did before (verified live, 65 =
65 and 140 = 140).

**Residual gap:** a condo unit HPD recorded under its own unit lot, with
no `bbl`, still can't be matched from the building's base lot.

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

## Citation labels come from real data, not idealised strings

The first parser was written against 2014-era violation text. Run against
5,000 **current** open violations, it put a **false "NYC Admin Code"
label on 1,348 of them**. It labeled city *rules* (`28 RCNY § 11-06`) as
the statute. HPD writes the legal basis in at least 42 different shapes.

The rule now: **label a citation only when its source is unambiguous, and
drop it otherwise.** A missing chip costs nothing; a mislabeled one is a
false statement about the law.

**Chips link to the law, by the same rule.** An Admin Code chip becomes a
link to `/law/<number>` only when the legal library holds exactly one
active, unrepealed section with that number, found with one batched query
per page (`law_service.linkable_citations`). § 26-1301 is never
auto-linked, because Title 26 has two of them. If the library can't be
read (it is empty until the 20260929 migration and first load), every chip
stays plain text and the page is otherwise unchanged. Linked chips are
underlined, so a tappable chip and a plain one never look the same. See
[law-page.md](law-page.md).

- `27-2xxx`: Housing Maintenance Code, identified by the number itself.
- Any other `NN-NN`: Admin Code only when the text says HMC or ADM CODE
  right after it, and RCNY only when it says RCNY. "28 RCNY" keeps its
  title; a bare "RCNY" doesn't get one guessed.
- Multiple Dwelling Law: bare section numbers, **only inside the leading
  citation clause, and only when that clause names the MDL**. Never from
  the body, which is full of numbers like "APT 5" and "3rd STORY".
- Fire Code: deliberately left unlabeled (out of scope).

`tests/fixtures/hpd_violation_descriptions.json` holds 135 real
descriptions covering every clause shape in the sample. The tests hold the
parser to zero false labels and zero leftover punctuation on all of them.

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

Tightened 2026-09-29. A browser sweep found that a pending building prompt
still auto-sent when the tenant ignored the continue chip and opened an
existing *empty* conversation from the sidebar. The prompt-carrying click
now also stores `pendingChatArmedAt`, and the chat page auto-sends only if
that stamp is less than two minutes old. Any other page load leaves the
prompt waiting behind the continue chip. Landing back on the greeting (for
example after a create that failed with a 429) clears the stamp, so a
failed click cannot arm a later one.

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
