"""Which language a page is shown in, and how text gets translated.

WHY THIS EXISTS (owner, 2026-10-11)
The site is to be readable in English, Spanish, Chinese, Swahili and
French. This module is the foundation: choosing the language, switching
it, and the helpers every other module translates through. English is the
source language and behaves exactly as before.

A LANGUAGE IS OFFERED ONLY ONCE IT IS LIVE
LIVE_LANGUAGES starts as English alone. The others are defined, with
catalogues under translations/, but none appears in the menu and none is
ever chosen automatically until it is added to LIVE_LANGUAGES. Adding a
language is a one-line change plus a log entry saying how it was
translated. PREVIEW_LANGUAGES (an environment variable, comma separated)
turns languages on for a local or staging run; it is not for production.

TRANSLATIONS ARE MACHINE-MADE, AND ENGLISH STAYS THE REFERENCE
(owner, 2026-10-11, replacing the earlier "a fluent reader approves each
language" gate). Every translated page says so and links to the same page
in English. Wording marked REVIEW in the catalogues is the high-stakes
wording: the disclaimers in branding.py, the RA-81 review screen, what a
tenant agrees to when deleting an account. It is shown with its English
original underneath, so nobody has to rely on the translation alone. A
live language may still not have a REVIEW entry that is untranslated or
fuzzy (tests/test_i18n.py).

CHINESE IS SIMPLIFIED ONLY (owner, 2026-10-11). zh_Hant stays defined but
is never made live, and a browser asking for Traditional Chinese gets
Simplified rather than English.

HOW THE LANGUAGE IS CHOSEN, IN ORDER
  1. the signed-in tenant's saved choice (their account, so it follows
     them to another device; kept in the session after sign-in);
  2. the choice remembered on this device (a cookie, set only when the
     visitor uses the language menu);
  3. the browser's own language list (owner, 2026-10-11);
  4. English.
A language that is not live is skipped at every step.

ENGLISH FOR ONE PAGE
A link can carry ?lang=en (or any live language): it applies to that one
request and is not remembered. The machine-translation notice uses it for
"read this page in English", so the link needs no JavaScript, no form and
no cookie.

HOW TEXT IS TRANSLATED
  * Templates and request-time Python call _("text") (gettext).
  * Wording that lives in module-level constants (branding.py, the labels
    in case_context.py, password_safety.py) stays a plain English string
    there, so everything that compares or reuses it keeps working, and is
    translated where it is USED, with _() or translated(). Those constants
    reach the catalogues through extract_constants (see babel.cfg), which
    reads their values rather than their source, so f-strings work too.
  * Without Flask-Babel set up (a bare app in a test), _() returns the
    text unchanged instead of failing.

WHAT IS NEVER TRANSLATED HERE
Statutes, HPD violation text, agency and officials' names, and anything a
tenant or the assistant wrote. Those are shown as published or as written.
"""

from __future__ import annotations

import importlib
import os
from urllib.parse import urlsplit

import flask_babel
from flask import current_app, redirect, request, session
from markupsafe import Markup

DEFAULT_LANGUAGE = "en"

# code (the catalogue's folder name) -> (its name in itself, the value for <html lang>).
# Chinese is two entries because Simplified and Traditional are different
# scripts with different browser fonts; bare "zh" would let the browser guess.
LANGUAGES = {
    "en": ("English", "en"),
    "es": ("Español", "es"),
    "zh_Hans": ("简体中文", "zh-Hans"),
    "zh_Hant": ("繁體中文", "zh-Hant"),
    "sw": ("Kiswahili", "sw"),
    "fr": ("Français", "fr"),
}

# Languages a tenant can actually get. See the module docstring before
# adding one: its catalogue must be filled and compiled (machine
# translation, no fluent-reader gate since 2026-10-11).
LIVE_LANGUAGES = ("en",)

COOKIE_NAME = "lang"
COOKIE_MAX_AGE = 365 * 24 * 3600
SESSION_KEY = "language"
METADATA_KEY = "language"

_TRADITIONAL_REGIONS = {"tw", "hk", "mo"}


def enabled() -> tuple[str, ...]:
    """Live languages, plus any switched on for preview by the environment."""
    preview = [code.strip() for code in os.environ.get("PREVIEW_LANGUAGES", "").split(",")]
    return tuple(code for code in LANGUAGES if code in LIVE_LANGUAGES or code in preview)


def _usable(code) -> str | None:
    return code if isinstance(code, str) and code in enabled() else None


def from_accept_language(header: str | None) -> str | None:
    """The first live language in a browser's Accept-Language list."""
    choices = []
    for position, part in enumerate((header or "").split(",")):
        tag, _, quality = part.strip().partition(";q=")
        try:
            weight = float(quality) if quality else 1.0
        except ValueError:
            weight = 0.0
        if tag and weight > 0:
            choices.append((-weight, position, tag.lower().replace("_", "-")))
    for _, _, tag in sorted(choices):
        pieces = tag.split("-")
        if pieces[0] == "zh":
            # A script named outright wins over the region ("zh-Hans-HK").
            traditional = "hans" not in pieces and ("hant" in pieces or bool(_TRADITIONAL_REGIONS & set(pieces)))
            # Traditional is not offered (owner, 2026-10-11): a reader of
            # Traditional Chinese is better served by Simplified than English.
            code = (_usable("zh_Hant") if traditional else None) or _usable("zh_Hans")
        else:
            code = _usable(pieces[0])
        if code:
            return code
    return None


def current() -> str:
    """The language for this request (see the order in the module docstring)."""
    try:
        return (
            _usable(request.args.get("lang"))
            or _usable(session.get(SESSION_KEY))
            or _usable(request.cookies.get(COOKIE_NAME))
            or from_accept_language(request.headers.get("Accept-Language"))
            or DEFAULT_LANGUAGE
        )
    except RuntimeError:  # no request: a script, or a test calling a helper directly
        return DEFAULT_LANGUAGE


def html_lang() -> str:
    return LANGUAGES[current()][1]


def remember_from_account(metadata) -> None:
    """At sign-in: adopt the language saved on the account, if it is live."""
    saved = _usable(metadata.get(METADATA_KEY)) if isinstance(metadata, dict) else None
    if saved:
        session[SESSION_KEY] = saved
    else:
        session.pop(SESSION_KEY, None)


def mark(message):
    """Marks a string for the catalogues without translating it (N_).

    For wording raised or stored far from where it is shown, such as an
    error message in a service module. It stays plain English there and is
    translated with gettext() at the point it reaches a page.
    """
    return message


def gettext(message, **variables):
    """Translate `message` for this request; `%(name)s` placeholders are filled after."""
    try:
        text = flask_babel.gettext(message)
    except Exception:  # noqa: BLE001 -- no Babel on this app, or no request: English
        text = message
    return text % variables if variables else text


_ = gettext


def markup_gettext(message, **variables):
    """gettext for templates: the result is page markup, as the text was
    when it sat in the template.

    Template text may hold entities ("&amp;", "&rsaquo;") and apostrophes
    that must reach the page exactly as written, so the translated text is
    trusted like template source. Values filled into it are escaped. A
    test keeps "<" and ">" out of every translation.
    """
    text = Markup(gettext(message))
    return text % variables if variables else text


# The link back to English is deliberately not translated: it is for the
# reader who wants the reference text, and it names the language in itself.
ENGLISH_LINK_LABEL = "Read this page in English"
_ORIGINAL = Markup(
    '%s <span class="english-original" lang="en" dir="ltr" '
    'style="display:block;font-size:0.85em;opacity:0.85;margin-top:2px">%s</span>'
)


def with_original(message, **variables):
    """High-stakes wording: the translation, with its English original beneath.

    English is the reference (owner, 2026-10-11): translations are
    machine-made, so wording a tenant relies on (the disclaimers, privacy
    wording, the password rule, the RA-81 review screen) is never shown in
    translation alone. On an English page, or where there is no
    translation, this is just the English text, unchanged.
    """
    english = message % variables if variables else message
    if not message or current() == DEFAULT_LANGUAGE:
        return english
    shown = gettext(message, **variables)
    if shown == english:
        return english
    return _ORIGINAL % (shown, english)


def english_if_translated(message) -> str:
    """The English original when the page shows a translation of it, else ''.
    For scripts, which build the two lines themselves."""
    if current() == DEFAULT_LANGUAGE or gettext(message) == message:
        return ""
    return message


def english_url() -> str:
    """This page, in English, for this one request."""
    from urllib.parse import urlencode

    args = request.args.to_dict(flat=False)
    args["lang"] = [DEFAULT_LANGUAGE]
    return request.path + "?" + urlencode(args, doseq=True)


def translated(value, keys=None, original=False):
    """A copy of a constant with its strings translated.

    Walks lists and dicts. In a dict only `keys` are translated when given
    (so a resource's URL, phone number and official name stay as they are).
    `original=True` is for high-stakes wording: see with_original().
    """
    if isinstance(value, str):
        if not value:
            return value
        return with_original(value) if original else gettext(value)
    if isinstance(value, (list, tuple)):
        return [translated(item, keys, original) for item in value]
    if isinstance(value, dict):
        return {k: (translated(v, keys, original) if keys is None or k in keys or isinstance(v, (list, dict)) else v)
                for k, v in value.items()}
    return value


def format_date(value, pattern="long") -> str:
    """'October 11, 2026' in English; the language's own form otherwise."""
    if value is None:
        return ""
    english = f"{value.strftime('%b' if pattern == 'medium' else '%B')} {value.day}, {value.year}"
    if current() == DEFAULT_LANGUAGE:
        return english
    try:
        return flask_babel.format_date(value, pattern)
    except Exception:  # noqa: BLE001
        return english


def menu() -> list[dict]:
    """What the language menu offers; empty unless there is a choice to make."""
    codes = enabled()
    if len(codes) < 2:
        return []
    now = current()
    return [{"code": code, "name": LANGUAGES[code][0], "lang": LANGUAGES[code][1], "current": code == now} for code in codes]


def init_app(app) -> None:
    """Set up translation for an app (the real one and every test app).

    Called from branding.register(), which both already call, so a bare
    test app renders through the same machinery as production.
    """
    if "babel" not in app.extensions:
        app.config.setdefault("BABEL_DEFAULT_LOCALE", DEFAULT_LANGUAGE)
        app.config.setdefault("BABEL_TRANSLATION_DIRECTORIES",
                              os.path.join(os.path.dirname(os.path.abspath(__file__)), "translations"))
        flask_babel.Babel(app, locale_selector=current)
    app.jinja_env.globals.update(
        _=markup_gettext, gettext=gettext, html_lang=html_lang, language_menu=menu, format_date=format_date,
        original=with_original, english_url=english_url, english_link_label=ENGLISH_LINK_LABEL,
    )


def _same_site() -> bool:
    """The language form carries no token (a token needs a session cookie,
    and most readers of the public pages have none), so it accepts only a
    request the browser says came from one of our own pages. All a forged
    one could do is change which language a page is shown in."""
    fetch_site = request.headers.get("Sec-Fetch-Site")
    if fetch_site:
        return fetch_site == "same-origin"
    origin = request.headers.get("Origin")
    return bool(origin) and urlsplit(origin).netloc == request.host


def _safe_next(target) -> str:
    if isinstance(target, str) and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return "/"


def register(bp) -> None:
    from rate_limit import limiter

    @bp.route("/language", methods=["POST"])
    @limiter.limit("30 per minute")
    def set_language():
        """Remember a language choice: a cookie on this device and, for a
        signed-in tenant, on their account so it follows them."""
        response = redirect(_safe_next(request.form.get("next")), code=303)
        code = _usable(request.form.get("language"))
        if not code or not _same_site():
            return response
        response.set_cookie(
            COOKIE_NAME, code, max_age=COOKIE_MAX_AGE, samesite="Lax", httponly=True,
            secure=bool(current_app.config.get("SESSION_COOKIE_SECURE")),
        )
        if session.get("user_id"):
            session[SESSION_KEY] = code
            try:
                service = current_app.config["SUPABASE_SERVICE"]
                tokens = service.update_user_metadata(
                    session.get("access_token"), session.get("refresh_token"), {METADATA_KEY: code})
                if tokens:
                    session["access_token"], session["refresh_token"] = tokens
            except Exception as exc:  # noqa: BLE001 -- the cookie already holds the choice
                current_app.logger.warning("Could not save a language choice to the account (%s).", type(exc).__name__)
        return response


# ---------------------------------------------------------------------------
# Getting module-level constants into the catalogues
# ---------------------------------------------------------------------------


def extract_constants(fileobj, keywords, comment_tags, options):
    """A Babel extractor: yields the VALUES of named module constants.

    babel.cfg lists, per file, `names` (the constants), optionally `keys`
    (the only dict keys to take) and `review = true`, which marks every
    string from that file with a "REVIEW" translator comment: high-stakes
    wording, which pages show together with its English original.
    """
    module = importlib.import_module(os.path.splitext(os.path.basename(fileobj.name))[0])
    names = [n.strip() for n in options.get("names", "").split(",") if n.strip()]
    keys = {k.strip() for k in options.get("keys", "").split(",") if k.strip()} or None
    comments = ["REVIEW: legal or high-stakes wording; shown with its English original beneath it."] if options.get("review") == "true" else []

    def walk(value):
        if isinstance(value, str):
            if value.strip():
                yield value
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from walk(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                if keys is None or key in keys or isinstance(item, (list, dict)):
                    yield from walk(item)

    seen = set()
    for name in names:
        for text in walk(getattr(module, name)):
            if text not in seen:
                seen.add(text)
                yield 1, "", text, [f"{name}"] + comments
    # Babel gives each file to one extractor only, so the ordinary calls in
    # the same file (gettext("..."), mark("...")) are read here too.
    from babel.messages.extract import extract_python

    fileobj.seek(0)
    yield from extract_python(fileobj, keywords, comment_tags, options)
