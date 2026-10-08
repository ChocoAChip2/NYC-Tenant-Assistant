# Frontend notes

The templates in `templates/` deliberately contain **no HTML, CSS or JS
comments**. Everything in this app's frontend is inline in the templates —
there is no build step, no bundler, and no separate `.css`/`.js` files — so
every comment written in a template is served verbatim to anyone who opens
View Source or DevTools. The reasoning that used to live in those comments
lives here instead.

That trade has a cost worth naming: the warnings are no longer next to the
code they're warning about. Several of the rules documented here look
pointless or redundant in isolation and have already been "cleaned up" and
re-broken once. **Read [css-gotchas.md](css-gotchas.md) before deleting a
CSS rule you don't recognise in these templates.**

## Files

| Doc | Covers |
| --- | --- |
| [css-gotchas.md](css-gotchas.md) | Load-bearing CSS rules that look removable and are not |
| [chat-page.md](chat-page.md) | `chat.html` — sidebar, composer, rename, suggestion chips, motion |
| [auth-and-settings-pages.md](auth-and-settings-pages.md) | `login`, `signup`, `forgot_password`, `reset_password`, `settings` |
| [branding-and-disclaimers.md](branding-and-disclaimers.md) | The product name, the ST mark, and the legal notices |
| [building-lookup.md](building-lookup.md) | `/building`: the public violation lookup, its honesty rules, and the chat hand-off |
| [law-page.md](law-page.md) | `/law/<citation>`: one section of official law, verbatim, and the building-page chip links |
| [info-tips.md](info-tips.md) | The "i" button that holds detail, and how short page copy should be |
| [area-lookup.md](area-lookup.md) | The street + borough search (front page), officials refresh, and the Resources tab |
| [case-workspace.md](case-workspace.md) | The per-chat "Your situation" panel and reviewed RA-81 drafts |

For *why a feature exists* (as opposed to how it's built), see `log/` —
one file per shipped branch, with the requirement it came from.

## Conventions across all templates

**No shared layout.** Every template is standalone and repeats the design
tokens, the `.password-toggle` styles, and the theme scripts. This is
deliberate but it means **a palette change has to be made in every page template**
(`building`, `chat`, `error`, `forgot_password`, `law`, `learn_more`,
`login`, `reset_password`, `settings`, `signup`), in the light block and
both dark blocks. There is no inheritance to catch a
missed one.

**Design tokens.** A cool-neutral, civic-blue palette — one accent color
reused everywhere, chosen to sit in the same register as nyc.gov / ny.gov
without copying them. `--bg`, `--surface`, `--border`, `--text`, `--muted`,
`--accent`, `--accent-hover`, `--accent-tint`, `--on-accent`, plus
`--error`/`--warning`/`--success` and their `-tint` pairs.

**Text on an accent fill uses `--on-accent`, never `#fff`.** White on the
dark-mode accent is 2.96:1, which fails WCAG AA. `--on-accent` is white in
light mode and near-black in dark. A test fails if hardcoded white comes
back on a themed page. See [building-lookup.md](building-lookup.md#--on-accent).

**Theming.** Every page follows the browser: light by default, dark when
the device asks for it, and dark is **AMOLED black** (`--bg: #000000`,
`--surface: #0a0a0a`), so an OLED screen switches those pixels off. Each
page carries:

- `<meta name="color-scheme" content="light dark">` so form controls,
  scrollbars and the date picker match before any CSS loads;
- two `theme-color` metas (`#eef1f5` light, `#000000` dark) for the
  mobile browser bar;
- an inline `<script>` in `<head>`, *before* `<style>`, that reads
  `localStorage.theme` and sets `data-theme` on `<html>`. It runs
  pre-paint so switching pages never flashes the wrong theme.

Three states:

- `data-theme="light"` / `"dark"` — an explicit choice from Settings; wins in both directions.
- no attribute — no choice saved yet, so `@media (prefers-color-scheme: dark)` decides.

The media-query block is guarded as `:root:not([data-theme="light"])` so an
explicit light choice still beats a dark OS setting, and the same tokens are
repeated under `:root[data-theme="dark"]` so an explicit dark choice beats a
light OS setting. The two dark blocks must stay identical; a test compares
them on every page, checks `--bg` is `#000000`, and checks the dark text
tokens against WCAG AA.

**Icons are inline SVG, never emoji.** An emoji renders as a fixed-color
picture that ignores CSS entirely, so it looked identical — and equally
illegible — in both light and dark mode. The SVGs use
`stroke="currentColor"` so they inherit their element's themed `color`.

**CSRF.** Every `<form>` carries `<input type="hidden" name="csrf_token">`.
The one exception is `#chat-form` in `chat.html`, which posts via
`fetch()`/JSON and so has nowhere to put a form field; it reads the token
from `<meta name="csrf-token">` in `<head>` into an `X-CSRFToken` header,
which Flask-WTF's `CSRFProtect` validates the same way.

**Submit buttons disable on submit** on every page, swapping their label to
a progress state ("Logging in…", "Saving…"). On a slow connection an
enabled button invites a second click, which means a second login attempt,
a second signup, or a second password-reset email.

**Reduced motion.** Everything animated is disabled under
`@media (prefers-reduced-motion: reduce)`, and the smooth-scroll call
checks `matchMedia` in JS because a media query can't reach `scrollTo`'s
`behavior` option. This is an accessibility setting — motion can trigger
nausea and vestibular symptoms — not a preference to override because the
animation is tasteful.

## Re-checking that the templates stay comment-free

```bash
# any comment that reaches the browser will show up here
python - <<'PY'
import re, glob
for path in glob.glob("templates/*.html"):
    html = open(path).read()
    if "<!--" in html:
        print("HTML comment in", path)
PY
```

`{# … #}` Jinja comments are fine and are *not* served — Jinja strips them
before the response is written. Each template starts with one, pointing
back here.
