# `/law/<citation>`: one section of official law

`templates/law.html`, served by `routes.law_section`, reading through
`law_service.py`. Public, like `/building`, because a citation has to open
for anyone it is shown to.

## What the page promises

- **The text is the official text.** It is what American Legal Publishing,
  the City's official publisher, publishes, loaded by `tools/corpus` and
  hash-checked by the database on every write. The page never paraphrases
  it. It sits in its own serif block (`.law-text`) so the law is visually
  distinct from our words around it.
- **Provenance on the page.** Every section shows its source, the effective
  date of its latest amendment (from the publisher's history line, or "No
  amendment date in the official history", never a guess), the date a
  refresh last matched it against the official code, and a link to the
  section's page on the official site. A row without an https source URL
  is not shown at all.
- **Publisher's notes are labelled as not law.** Editor's notes are kept
  separate by the parser; the page lists them under "Publisher's notes"
  with "Not part of the law."
- **Nothing silently disappears.** A repealed section shows a "This section
  has been repealed" notice with its history. A section that has left the
  official code (`status = 'missing_from_source'`, which the refresh sets
  instead of deleting) is still shown, under "No longer in the official
  code ... Don't rely on it as current law." Old links keep working and
  can't be mistaken for current law.
- **Two sections, one number.** Title 26 has two § 26-1301s. If the
  library holds more than one section with a number, the page shows all of
  them with a warning to check the chapter above each.
- **Plain failures.** A malformed number is a 404 without touching the
  database. A number we don't hold is a 404 that says which laws we do
  hold and points at the official publisher. A library that can't be read
  (including before the 20260929 migration) is a 503 with the same pointer.

`LAW_PAGE_NOTE` (in `branding.py`, like all legal wording) sits under the
text: this is the law as written, how it applies depends on the facts, not
legal advice. The standing footer disclaimer is there too.

## The index (`/law`)

`templates/law_index.html`, served by `routes.law_index`, reading through
`law_service.get_index`. Public and rate-limited like the section page. It
exists so someone without a link can find a section; it is linked from the
section page, its not-found message and Resources, not from the header.

- **Every section, grouped by law.** City law first, then State, one group
  per law in `tools/corpus/registry.py` order, sections in number order.
  The group heading is the registry name up to its bracket ("Housing
  Maintenance Code") with the bracketed part beside it in muted text. A
  source key is never shown; rows from a source the registry doesn't know
  go under "Other sections".
- **Groups are native `<details>`, closed.** The Housing Maintenance Code
  alone is 211 sections. Native elements mean keyboard and screen-reader
  behaviour come from the browser and nothing needs JavaScript. The
  browser's find-in-page opens a closed group in current browsers.
- **Nothing is hidden.** Repealed sections carry a "Repealed" tag, ones
  that left the code "No longer in the code", same rule as the section
  page.
- **Entries link by number.** Where two sections share a number, both
  entries open the section page, which shows every match.
- **"Go to a section"** is a plain GET form (`/law?section=27-2005`). The
  route tidies the number (`§`, spaces, a trailing full stop, capitals) and
  redirects to the section page; something that isn't shaped like a section
  number comes back as a 400 with a message beside the box. Whether the
  library holds the number is left to the section page.
- **One read, shared.** The list comes from the same hourly in-memory
  index as the building page's chip links (`law_service._library_index`),
  so the page usually makes no library call. A library that can't be read
  and was never loaded is a 503 with the official sites; an empty library
  is a plain notice.

## Styling

Standalone like every template. The tokens, header, buttons and alerts are
copied from `building.html`, so a palette change there must be made here
too. `law.html` and `law_index.html` (which copies `law.html`'s head) are
in the contrast test's `THEMED` list.

## Chip links from `/building`

See [building-lookup.md](building-lookup.md): a chip links here only when
the number is unambiguous in the library, and a library failure leaves
every chip as plain text.
