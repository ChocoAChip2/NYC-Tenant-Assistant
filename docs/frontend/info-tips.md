# Info tips: the "i" button

**Rule (owner, 2026-10-02):** pages show only the words a tenant needs to
act. Anything extra — the reason behind a rule, an exception, a caveat —
goes behind a small "i" button next to the short version.

## Writing copy

- Lead lines: one sentence, about 15 words or fewer, plain words, verb
  first where it fits ("Download all your conversations…").
- Put the part someone *must* see on the page (a deadline, "not legal
  advice", "can't be undone"). Put the explanation behind the "i".
- Don't repeat what a label, placeholder or button already says.
- **Say what's true for the tenant, not how it's built** (owner,
  2026-10-02). "Encrypted and never shared", not which service stores it,
  which model reads it or which key seals it. Those change as the app
  develops; public wording shouldn't have to. Same for errors: "The
  assistant is unavailable right now", never a config or vendor name.
  `tests/test_info_tips.py` fails if a template or public string in
  `branding.py` names the backend.
- **General, but still true.** A general promise has to hold for every way
  the data is handled. Profile details are never sent anywhere, so "never
  shared" is accurate there. Chat messages go to the AI provider to be
  answered, so no page says conversations are "never shared"; the Learn
  More page says "encrypted and never sold".
- Reference points: JustFix's tool cards (a 4–8 word title and one short
  line) and NYC HPD's pages (short sentences, detail on linked pages).

## Using it

`templates/_info.html` holds two macros:

```jinja
{% from "_info.html" import info as info_tip, info_assets %}
<p>Short line.{{ info_tip("The longer explanation.") }}</p>
<p>Short line.{% call info_tip(label="What our library holds") %}Detail with <a href="…">a link</a>.{% endcall %}</p>
…
{{ info_assets() }}   {# once, just before </body> #}
```

- It is imported **as `info_tip`** because `building.html` loops over a
  variable named `info`.
- Everything it renders is phrasing content (`<span>`, `<button>`), so it
  is valid inside a `<p>`. `<details>` was rejected for that reason: a
  browser closes the `<p>` before it and breaks the line.
- A partial that is `{% include %}`d (like `_requirement_profile.html`)
  imports the macro itself; imports don't pass through includes.
- `info_assets()` carries the CSS and JS. `tests/test_info_tips.py` fails if
  a page uses a tip without loading them, or loads them twice.

## Behaviour

- Click or tap toggles it; one tip is open at a time; a click elsewhere
  or Escape closes it (Escape returns focus to the button).
- `aria-expanded` on the button; the text is a `role="note"` right after
  it, so a screen reader reads it in place.
- The popover is nudged back inside the viewport (16px gutter) on narrow
  screens.
- Selectors are `.info-tip > .info-btn` on purpose: pages style `button`
  broadly (`.card button`, `form button`), and a plain `.info-btn` lost.
- Colours are the page tokens (`--surface`, `--border`, `--muted`,
  `--accent`), so it follows light/dark like everything else. In the amber
  banner the button takes the banner's colour (`currentColor`).
