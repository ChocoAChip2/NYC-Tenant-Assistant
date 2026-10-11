# Translations (five languages)

Started 2026-10-11. Code: `i18n.py`, `templates/_language.html`,
`babel.cfg`, `translations/`. English is the source language and is
unchanged.

## Where this stands

Done (foundation):
- Flask-Babel is set up for the real app and every test app.
- The language for a request is chosen and can be switched.
- Every page takes `<html lang>` from the choice and has a slot for the
  language menu.
- Translatable so far: all wording in `branding.py`, the password rule
  messages, every flash message in `routes.py`, the privacy page, dates.
- Empty catalogues exist for `es`, `fr`, `sw`, `zh_Hans`, `zh_Hant`.

Not done yet:
- **Template text is not wrapped.** Headings, labels, buttons and
  paragraphs written directly in the ~20 templates are still plain
  English, as are strings set from JavaScript, the JSON error messages,
  the lookup error titles, and messages raised in `profile_service`,
  `account_requirements`, `case_context`, `form_review` and
  `error_pages`. This is the next slice.
- No language has any translation. Nothing is live but English.
- The chat's reply-language tiebreaker, the new reply-language choices,
  the "quote the law in English" rule, the "not a translation" note on
  `/law` and the RA-81 character check.
- The menu overflows the header at 390 px on pages with a full nav
  (seen with preview languages on). It must be fixed before a language
  goes live.

## A language is offered only when it is live

`i18n.LIVE_LANGUAGES` is `("en",)`. Until a language is added there it is
not in the menu, is never picked from the browser's settings, and cannot
be set through the switch route or a cookie.

Going live needs, for that language:
1. its catalogue filled in;
2. every entry marked `REVIEW` approved by a fluent reader (owner,
   2026-10-11). These are the disclaimers, the privacy wording, the
   password rule and the help-resource descriptions today; the RA-81
   review screen and account deletion join them when wrapped;
3. `pybabel compile` run, and the compiled files deployed;
4. the code added to `LIVE_LANGUAGES`, with a log entry naming who
   reviewed it. Two tests enforce the pairing: a live language may not
   have an untranslated or fuzzy `REVIEW` entry, and a catalogue that is
   not live may not contain translations.

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

- Templates and request-time Python: `_("text")`, with `%(name)s`
  placeholders filled by keyword (`_("Wait %(wait)s.", wait=...)`). Never
  an f-string: the catalogue is keyed by the exact text.
- **Module-level wording stays plain English strings** (`branding.py`,
  `password_safety.py`). ~570 existing assertions and several callers
  compare or reuse those strings, and a lazy translation object is not a
  `str`. They are translated where they are used: the branding context
  processor, `flash(_(...))`, `i18n.translated(list, keys=...)`.
- Those constants reach the catalogue by value, not by source:
  `i18n.extract_constants`, configured in `babel.cfg`, imports the module
  and yields the named constants. That is why f-strings in `branding.py`
  work, and why each file's strings can be marked `REVIEW` as a block.
- Dates go through `format_date()`. English output is exactly what
  `strftime` produced before.

## What is never translated

Statutes and section titles, HPD violation text, agency and officials'
names, URLs and phone numbers, the RA-81 PDF itself, and anything a tenant
or the assistant wrote (chat, titles, "Your data"). The product name is a
name.

## Updating the catalogues

```
PYTHONPATH=. venv/bin/pybabel extract -F babel.cfg -c REVIEW -o translations/messages.pot --no-location --sort-output .
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
