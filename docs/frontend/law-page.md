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

## Styling

Standalone like every template. The tokens, header, buttons and alerts are
copied from `building.html`, so a palette change there must be made here
too. `law.html` is in the contrast test's `THEMED` list.

## Chip links from `/building`

See [building-lookup.md](building-lookup.md): a chip links here only when
the number is unambiguous in the library, and a library failure leaves
every chip as plain text.
