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
- **Describe the purpose and user controls, not implementation** (owner,
  2026-10-06). The account note says "Used for your account" and points to
  Settings. Keep storage architecture, providers, algorithms and configuration
  out of both visible copy and rendered source. Errors must not interpolate
  service exception text. Tests cover both the templates and failure responses.
- **General, but still true.** Do not make blanket "never shared" claims about
  chat data that is processed to generate a response. Keep user-facing information
  useful without publishing internal security details. Frontend URLs, ordinary
  JavaScript and per-user anti-forgery fields remain visible by design; hiding
  them is not an access-control mechanism.
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
