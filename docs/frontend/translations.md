# Translations (five languages)

Started 2026-10-11. Code: `i18n.py`, `templates/_language.html`,
`babel.cfg`, `translations/`. English is the source language and is
unchanged.

## Where this stands

Done:
- Flask-Babel is set up for the real app and every test app.
- The language for a request is chosen and can be switched.
- Every page takes `<html lang>` from the choice and has a slot for the
  language menu.
- **All page text is wrapped** (2026-10-11): template text and
  attributes, strings set from JavaScript, JSON error messages, lookup
  error titles, flash messages, and the messages raised in
  `profile_service`, `account_requirements`, `case_context`,
  `form_review`, `form_service`, `area_service`, `building_service` and
  `error_pages`. 699 messages; 117 marked `REVIEW`.
- Catalogues exist for `es`, `fr`, `sw`, `zh_Hans`, `zh_Hant`, all empty.

Not done yet:
- The English-reference display: the machine-translation notice with a
  link to the English page, and each `REVIEW` string shown with its
  English original.
- Filling the four live catalogues by machine translation.
- Chat: new reply-language choices, the interface-language tiebreaker,
  the "quote the law in English" rule, the note on `/law`, the fact-sheet
  links.
- Going live.

Known rough edges in what is wrapped:
- A sentence with a link or bold text in the middle is several messages
  (the text before, the link, the text after). Word order can't change
  across those pieces, which will read awkwardly in some languages.
- The language menu sits on its own row under the header at 700 px and
  below. Looked at on one page only.

## A language is offered only when it is live

`i18n.LIVE_LANGUAGES` is `("en",)`. Until a language is added there it is
not in the menu, is never picked from the browser's settings, and cannot
be set through the switch route or a cookie.

**Translations are machine-made and English stays the reference** (owner,
2026-10-11; this replaced an earlier plan to have a fluent reader approve
each language). So:
- every translated page will say it is machine-translated and link to the
  same page in English;
- wording marked `REVIEW` is the high-stakes wording (disclaimers, privacy
  wording, the password rule, the RA-81 review screen's labels and
  messages) and will be shown with its English original beneath it;
- sign-in, confirmation, reset and code emails stay English.

Going live needs, for that language: its catalogue filled, nothing fuzzy,
`pybabel compile` run with the compiled files committed, and the code
added to `LIVE_LANGUAGES` with a log entry saying how it was translated.
A test refuses a live language with an untranslated or fuzzy `REVIEW`
entry.

**Chinese is Simplified only** (owner, 2026-10-11). `zh_Hant` stays
defined but is never made live and its catalogue stays empty (tested). A
browser asking for Traditional Chinese gets Simplified rather than
English.

To look at a language before it is live, run locally with
`PREVIEW_LANGUAGES=es` (comma separated). Not for production.

## How the language is chosen

1. The signed-in tenant's saved choice (`user_metadata.language`, copied
   into the session at sign-in).
2. The choice remembered on this device (the `lang` cookie).
3. The browser's `Accept-Language` list (owner, 2026-10-11).
4. English.

Chinese is two languages here, `zh_Hans` and `zh_Hant` (`<html
lang="zh-Hans">` / `zh-Hant`), because a bare `zh` lets the browser guess
the script and pick the wrong glyphs. From a browser list: a script named
outright wins, then Taiwan, Hong Kong and Macau mean Traditional, anything
else Simplified.

## The menu and the switch

`language_form()` renders a plain form (a select and a button, each
language named in itself) that posts to `/language`. It works without
JavaScript. It renders nothing while only one language is live.

`/language` carries no CSRF token, like the click counter: a token needs a
session cookie, and the menu is on every public page, read mostly by
people who have none. It accepts only a request the browser marks as
coming from our own pages, is rate limited, and can only set which
language pages are shown in. It sets the `lang` cookie (one year,
HttpOnly, SameSite=Lax) and, for a signed-in tenant, saves the choice to
their account. The privacy page says so.

## How text is translated

- **In templates**, `_("text")` returns page markup: the translated text
  is trusted exactly as the text was when it sat in the template, so
  entities and apostrophes reach the page unchanged and English output is
  byte for byte what it was. Values filled in with `%(name)s` are escaped.
  Tests keep `<` and `>` out of every translation and check that each one
  keeps its source's placeholders.
- `gettext("text")` in a template returns plain text, for places that
  escape it themselves: arguments to the "i" button, values inside
  `{{ ... }}` expressions, and strings handed to JavaScript with
  `|tojson`.
- **In scripts**, a string shown to a person is written
  `{{ gettext("text")|tojson }}`. Where the script builds a sentence, the
  message holds a placeholder and the script fills it:
  `{{ gettext("%(count)s characters left")|tojson }}.replace("%(count)s", n)`.
  Dates formatted in scripts use the page's language.
- **In Python at request time**: `_("text")`, with `%(name)s`
  placeholders filled by keyword. Never an f-string: the catalogue is
  keyed by the exact text.
- **Plurals** are two whole messages chosen in the template ("%(count)s
  home" / "%(count)s homes"), not a word with an "s" added.
- **Module-level wording stays plain English strings** (`branding.py`,
  `password_safety.py`, the labels in `case_context.py` and
  `form_review.py`, the error page table). Existing assertions and callers
  compare or reuse those strings, and a lazy translation object is not a
  `str`. They are translated where they are used.
- Those constants reach the catalogue by value: `i18n.extract_constants`
  (configured per file in `babel.cfg`) imports the module and yields the
  named constants, then also reads the ordinary `gettext()` and `mark()`
  calls in the same file, because Babel gives each file to one extractor.
- `mark("text")` puts a string in the catalogue without translating it,
  for wording passed on as data (a field label handed to a message).
- Messages raised in service modules are translated where they are raised
  (there is always a request by then) and shown as they arrive.
- Dates go through `format_date()`. English output is exactly what
  `strftime` produced before.

## What is never translated

Statutes and section titles, HPD violation text, agency and officials'
names, URLs and phone numbers, the RA-81 PDF itself, and anything a tenant
or the assistant wrote (chat, titles, "Your data"). The product name is a
name.

## Updating the catalogues

```
PYTHONPATH=. venv/bin/pybabel extract -F babel.cfg -c REVIEW -k mark -o translations/messages.pot --no-location --sort-output .
venv/bin/pybabel update -i translations/messages.pot -d translations --no-fuzzy-matching
venv/bin/pybabel compile -d translations
```

`PYTHONPATH=.` is needed so the extractor can import `i18n`. Compiled
`.mo` files are not in the repository yet; decide at the first live
language whether to commit them or compile in the build.

## Checked on HCR's site (2026-10-11)

There is no translated RA-81. Fact Sheet #14 (rent reductions for
decreased services) is published in Spanish and Traditional Chinese, as a
guide only; filing stays on the English form. Worth linking beside the
form on Spanish and Chinese pages.
