"""Language choice, the language switch, and translation plumbing (i18n.py).

English is the only live language, so most of these tests switch others on
for the duration of a test and use a small throwaway Spanish catalogue
built in a temp directory. Nothing here judges a real translation: the
real catalogues are empty until a fluent reader has reviewed them.
"""

import datetime
import glob
import os
import re
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-for-i18n-tests")

import flask
from babel.messages.catalog import Catalog
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po

import branding
import i18n
import password_safety
from rate_limit import limiter
from routes import main_bp
from tests.app_test_support import configure_test_app
from tests.review_support import MemorySupabase, ReviewAI

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TEMPLATES = os.path.join(_ROOT, "templates")
_TRANSLATIONS = os.path.join(_ROOT, "translations")
ALL = tuple(i18n.LANGUAGES)
SAME = {"Sec-Fetch-Site": "same-origin"}

SPANISH = {
    branding.SHORT_DISCLAIMER: "SideKick Tidbit da información general, no asesoría legal.",
    branding.LEARN_MORE_LABEL: "Más información",
    "Language": "Idioma",
    "Change": "Cambiar",
    "Please provide both email and password.": "Escribe tu correo y tu contraseña.",
    "You can send a new code in %(wait)s.": "Puedes pedir otro código en %(wait)s.",
    "%(count)s seconds": "%(count)s segundos",
    "Full name": "Nombre completo",
    branding.PER_MESSAGE_DISCLAIMER: "Información general, no asesoría legal.",
}


def _catalogue_dir(test):
    folder = tempfile.TemporaryDirectory()
    test.addCleanup(folder.cleanup)
    catalogue = Catalog(locale="es")
    for source, target in SPANISH.items():
        catalogue.add(source, target)
    path = os.path.join(folder.name, "es", "LC_MESSAGES")
    os.makedirs(path)
    with open(os.path.join(path, "messages.mo"), "wb") as handle:
        write_mo(handle, catalogue)
    return folder.name


class _Case(unittest.TestCase):
    live = ALL

    def setUp(self):
        if getattr(limiter, "_storage", None) is not None:
            limiter.reset()
        patcher = mock.patch.object(i18n, "LIVE_LANGUAGES", self.live)
        patcher.start()
        self.addCleanup(patcher.stop)

    def app(self, service=None, translations=None):
        app = flask.Flask(__name__, template_folder=_TEMPLATES)
        app.secret_key = "t"
        app.config.update(SUPABASE_SERVICE=service if service is not None else MemorySupabase(), AI_SERVICE=ReviewAI(), TESTING=True)
        if translations:
            app.config["BABEL_TRANSLATION_DIRECTORIES"] = translations
        app.register_blueprint(main_bp)
        configure_test_app(app)
        return app


class WhatIsLiveTests(unittest.TestCase):
    def test_english_is_the_only_live_language(self):
        self.assertEqual(i18n.LIVE_LANGUAGES, ("en",))
        with mock.patch.dict(os.environ, {"PREVIEW_LANGUAGES": ""}):
            self.assertEqual(i18n.enabled(), ("en",))

    def test_preview_languages_are_an_explicit_switch(self):
        with mock.patch.dict(os.environ, {"PREVIEW_LANGUAGES": "es, zh_Hant, klingon"}):
            self.assertEqual(i18n.enabled(), ("en", "es", "zh_Hant"))

    def test_the_planned_languages_are_defined_each_named_in_itself(self):
        self.assertEqual(set(i18n.LANGUAGES), {"en", "es", "zh_Hans", "zh_Hant", "sw", "fr"})
        self.assertEqual(i18n.LANGUAGES["es"], ("Español", "es"))
        self.assertEqual(i18n.LANGUAGES["zh_Hans"][1], "zh-Hans")
        self.assertEqual(i18n.LANGUAGES["zh_Hant"][1], "zh-Hant")
        self.assertEqual(i18n.LANGUAGES["sw"][0], "Kiswahili")
        self.assertEqual(i18n.LANGUAGES["fr"][0], "Français")


class EnglishOnlyTests(_Case):
    live = ("en",)

    def test_nothing_changes_for_a_visitor_whose_browser_prefers_another_language(self):
        client = self.app().test_client()
        response = client.get("/learn-more", headers={"Accept-Language": "es-US,es;q=0.9"})
        body = response.get_data(as_text=True)
        self.assertIn('<html lang="en">', body)
        self.assertIn(branding.SHORT_DISCLAIMER, body)
        self.assertNotIn("language-form", body)
        self.assertNotIn("Set-Cookie", response.headers)

    def test_no_page_shows_a_menu_with_only_one_language(self):
        client = self.app().test_client()
        for path in ("/login", "/signup", "/resources", "/privacy", "/learn-more", "/forgot-password"):
            with self.subTest(path=path):
                self.assertNotIn('action="/language"', client.get(path).get_data(as_text=True))

    def test_a_language_that_is_not_live_cannot_be_chosen(self):
        client = self.app().test_client()
        response = client.post("/language", data={"language": "es", "next": "/learn-more"}, headers=SAME)
        self.assertEqual(response.status_code, 303)
        self.assertNotIn("Set-Cookie", response.headers)
        client.set_cookie(i18n.COOKIE_NAME, "es")
        self.assertIn('<html lang="en">', client.get("/learn-more").get_data(as_text=True))

    def test_english_dates_read_as_they_always_have(self):
        with self.app().test_request_context("/"):
            self.assertEqual(i18n.format_date(datetime.date(2026, 10, 1)), "October 1, 2026")
            self.assertEqual(i18n.format_date(datetime.date(2026, 2, 3), "medium"), "Feb 3, 2026")
            self.assertEqual(i18n.format_date(None), "")


class ChoosingTheLanguageTests(_Case):
    def language(self, client, **headers):
        body = client.get("/learn-more", headers=headers).get_data(as_text=True)
        return re.search(r'<html lang="([^"]+)">', body).group(1)

    def test_order_is_account_then_device_then_browser_then_english(self):
        client = self.app().test_client()
        self.assertEqual(self.language(client), "en")
        self.assertEqual(self.language(client, **{"Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"}), "fr")
        client.set_cookie(i18n.COOKIE_NAME, "sw")
        self.assertEqual(self.language(client, **{"Accept-Language": "fr-FR"}), "sw")
        with client.session_transaction() as session:
            session[i18n.SESSION_KEY] = "es"
        self.assertEqual(self.language(client, **{"Accept-Language": "fr-FR"}), "es")

    def test_browser_lists_are_read_by_preference_and_chinese_by_script(self):
        cases = {
            "es-419,es;q=0.9,en;q=0.8": "es", "en-US,en;q=0.9,es;q=0.8": "en", "de-DE,fr;q=0.7,en;q=0.3": "fr",
            "zh-CN,zh;q=0.9": "zh_Hans", "zh": "zh_Hans", "zh-TW": "zh_Hant", "zh-HK,zh;q=0.9": "zh_Hant",
            "zh-Hant-US": "zh_Hant", "zh-Hans-HK": "zh_Hans", "sw-KE": "sw", "es;q=0, fr;q=0.5": "fr",
            "ja,ko;q=0.5": None, "": None, None: None, "es;q=abc": None, "*": None,
        }
        for header, expected in cases.items():
            with self.subTest(header=header):
                self.assertEqual(i18n.from_accept_language(header), expected)

    def test_a_browser_asking_for_traditional_chinese_gets_simplified_when_only_that_is_live(self):
        with mock.patch.object(i18n, "LIVE_LANGUAGES", ("en", "es", "zh_Hans")):
            for header in ("zh-TW", "zh-HK,zh;q=0.9", "zh-Hant", "zh-Hant-US,en;q=0.5", "zh-CN", "zh"):
                with self.subTest(header=header):
                    self.assertEqual(i18n.from_accept_language(header), "zh_Hans")
            self.assertEqual(i18n.from_accept_language("en,zh-TW;q=0.5"), "en")

    def test_junk_in_the_cookie_or_session_is_ignored(self):
        client = self.app().test_client()
        client.set_cookie(i18n.COOKIE_NAME, "<script>")
        with client.session_transaction() as session:
            session[i18n.SESSION_KEY] = ["es"]
        self.assertEqual(self.language(client), "en")

    def test_signing_in_adopts_the_language_saved_on_the_account(self):
        with self.app().test_request_context("/"):
            i18n.remember_from_account({i18n.METADATA_KEY: "fr"})
            self.assertEqual(flask.session[i18n.SESSION_KEY], "fr")
            i18n.remember_from_account({i18n.METADATA_KEY: "klingon"})
            self.assertNotIn(i18n.SESSION_KEY, flask.session)
            i18n.remember_from_account(None)
            self.assertNotIn(i18n.SESSION_KEY, flask.session)

    def test_every_page_template_takes_its_lang_from_the_choice(self):
        for path in glob.glob(os.path.join(_TEMPLATES, "*.html")):
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            name = os.path.basename(path)
            if name != "_language.html":  # its link to the English page is marked as English
                self.assertNotIn('lang="en"', source, name)
            if "<html" in source:
                self.assertIn('<html lang="{{ html_lang() }}">', source, name)
                self.assertIn("{{ language_form() }}", source, name)
                self.assertRegex(source, r"<body>\s*\{\{ translation_notice\(\) \}\}", name)


class SwitchingTests(_Case):
    def test_choosing_sets_a_long_lived_cookie_and_returns_to_the_page(self):
        client = self.app().test_client()
        response = client.post("/language", data={"language": "es", "next": "/resources?borough=Bronx"}, headers=SAME)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.location, "/resources?borough=Bronx")
        cookie = response.headers["Set-Cookie"]
        self.assertTrue(cookie.startswith("lang=es;"))
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn(f"Max-Age={i18n.COOKIE_MAX_AGE}", cookie)
        self.assertIn('<html lang="es">', client.get("/learn-more").get_data(as_text=True))

    def test_only_our_own_pages_can_switch_it(self):
        client = self.app().test_client()
        for headers in ({"Sec-Fetch-Site": "cross-site"}, {"Origin": "https://evil.example"}, {}):
            with self.subTest(headers=headers):
                response = client.post("/language", data={"language": "es"}, headers=headers)
                self.assertEqual(response.status_code, 303)
                self.assertNotIn("Set-Cookie", response.headers)
        self.assertIn("Set-Cookie", client.post("/language", data={"language": "es"}, headers={"Origin": "http://localhost"}).headers)

    def test_unknown_languages_and_outside_destinations_are_refused(self):
        client = self.app().test_client()
        for value in ("klingon", "", "en;rm", "ES"):
            self.assertNotIn("Set-Cookie", client.post("/language", data={"language": value}, headers=SAME).headers)
        for target in ("https://evil.example/", "//evil.example", "/\\evil", "javascript:alert(1)", None):
            response = client.post("/language", data={"language": "fr", "next": target} if target else {"language": "fr"}, headers=SAME)
            self.assertEqual(response.location, "/")

    def test_a_signed_in_tenant_also_has_it_saved_to_their_account(self):
        saved = []

        class Service(MemorySupabase):
            def update_user_metadata(self, access_token, refresh_token, metadata):
                saved.append((access_token, refresh_token, dict(metadata)))
                return "new-access", "new-refresh"

        client = self.app(Service()).test_client()
        with client.session_transaction() as session:
            session.update(user_id="review-user", user_email="t@example.invalid", access_token="a", refresh_token="r")
        client.post("/language", data={"language": "sw", "next": "/settings"}, headers=SAME)
        self.assertEqual(saved, [("a", "r", {"language": "sw"})])
        with client.session_transaction() as session:
            self.assertEqual(session[i18n.SESSION_KEY], "sw")
            self.assertEqual((session["access_token"], session["refresh_token"]), ("new-access", "new-refresh"))

    def test_if_the_account_cannot_be_updated_the_device_still_remembers(self):
        class Service(MemorySupabase):
            def update_user_metadata(self, *args):
                raise RuntimeError("auth down: internal detail")

        app = self.app(Service())
        client = app.test_client()
        with client.session_transaction() as session:
            session.update(user_id="review-user", user_email="t@example.invalid", access_token="a", refresh_token="r")
        with self.assertLogs(app.logger, level="WARNING") as logs:
            response = client.post("/language", data={"language": "fr"}, headers=SAME)
        self.assertTrue(response.headers["Set-Cookie"].startswith("lang=fr;"))
        self.assertNotIn("internal detail", "\n".join(logs.output))

    def test_a_visitor_who_is_not_signed_in_touches_no_account(self):
        service = MemorySupabase()
        service.update_user_metadata = mock.Mock()
        self.app(service).test_client().post("/language", data={"language": "es"}, headers=SAME)
        service.update_user_metadata.assert_not_called()

    def test_the_real_app_needs_no_token_for_it_and_still_needs_one_elsewhere(self):
        from app import create_app

        client = create_app().test_client()
        response = client.post("/language", data={"language": "es", "next": "/privacy"}, headers=SAME)
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers["Set-Cookie"].startswith("lang=es;"))
        self.assertEqual(len(response.headers.getlist("Set-Cookie")), 1, "the language cookie and nothing else")
        self.assertEqual(client.post("/logout").status_code, 400)


class TranslatedPagesTests(_Case):
    def setUp(self):
        super().setUp()
        self.client = self.app(translations=_catalogue_dir(self)).test_client()

    def test_spanish_pages_use_the_catalogue_and_english_ones_do_not(self):
        spanish = self.client.get("/learn-more", headers={"Accept-Language": "es"}).get_data(as_text=True)
        self.assertIn('<html lang="es">', spanish)
        self.assertIn(SPANISH[branding.SHORT_DISCLAIMER], spanish)
        english = self.client.get("/learn-more").get_data(as_text=True)
        self.assertIn(branding.SHORT_DISCLAIMER, english)
        self.assertNotIn(SPANISH[branding.SHORT_DISCLAIMER], english)
        self.assertNotIn("english-original", english)
        self.assertNotIn("translation-notice", english)

    def test_legal_wording_is_shown_with_its_english_original(self):
        spanish = self.client.get("/learn-more", headers={"Accept-Language": "es"}).get_data(as_text=True)
        self.assertRegex(spanish, re.escape(SPANISH[branding.SHORT_DISCLAIMER])
                         + r' <span class="english-original" lang="en" dir="ltr" style="[^"]+">'
                         + re.escape(branding.SHORT_DISCLAIMER) + "</span>")
        with self.client.application.test_request_context("/", headers={"Accept-Language": "es"}):
            self.assertEqual(i18n.with_original(branding.TOP_DISCLAIMER), branding.TOP_DISCLAIMER, "no translation: English alone, once")
            self.assertEqual(i18n.english_if_translated(branding.SHORT_DISCLAIMER), branding.SHORT_DISCLAIMER)
            self.assertEqual(i18n.english_if_translated(branding.TOP_DISCLAIMER), "")
            both = i18n.with_original("You can send a new code in %(wait)s.", wait="<b>9</b>")
            self.assertIn("Puedes pedir otro código en &lt;b&gt;9&lt;/b&gt;.", both)
            self.assertIn(">You can send a new code in &lt;b&gt;9&lt;/b&gt;.</span>", both)
        with self.client.application.test_request_context("/"):
            self.assertEqual(i18n.with_original(branding.SHORT_DISCLAIMER), branding.SHORT_DISCLAIMER)
            self.assertIsInstance(i18n.with_original(branding.SHORT_DISCLAIMER), str)
            self.assertEqual(i18n.english_if_translated(branding.SHORT_DISCLAIMER), "")

    def test_every_high_stakes_string_goes_through_the_original_helper(self):
        calls = []
        real = i18n.with_original

        def spy(message, **variables):
            calls.append(message)
            return real(message, **variables)

        import password_safety
        with mock.patch.object(i18n, "with_original", side_effect=spy):
            headers = {"Accept-Language": "es"}
            self.client.get("/privacy", headers=headers)
            self.client.get("/learn-more", headers=headers)
            self.client.get("/signup", headers=headers)
            self.client.post("/signup", data={"email": "t@example.invalid", "password": "abc", "first_name": "Ana",
                                              "last_name": "Rivera", "date_of_birth": "1990-05-17"}, headers=headers)
        expected = [branding.SHORT_DISCLAIMER, branding.PRIVACY_NOTE, branding.PRIVACY_NOTE_TITLE, branding.PRIVACY_LEDE,
                    branding.PRIVACY_ACCOUNT_TEXT, password_safety.RULE_MESSAGE]
        expected += [section["intro"] for section in branding.PRIVACY_SECTIONS]
        expected += [point for section in branding.PRIVACY_SECTIONS for point in section["points"]]
        expected += [point[key] for point in branding.FULL_DISCLAIMER_POINTS for key in ("lead", "detail")]
        expected += [resource["detail"] for resource in branding.HELP_RESOURCES]
        expected += [label for _key, label in password_safety.RULES]
        for text in expected:
            self.assertIn(text, calls, text[:50])

    def test_the_ra81_review_screen_puts_the_english_label_beside_each_translated_one(self):
        with open(os.path.join(_TEMPLATES, "form_review.html"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("<label>{{ original(label) }}", source)
        self.assertIn("<legend>{{ original(group) }}</legend>", source)
        with self.client.application.test_request_context("/", headers={"Accept-Language": "es"}):
            label = flask.render_template_string('<label>{{ original("Full name") }}</label>')
        self.assertRegex(label, r'<label>Nombre completo <span class="english-original" lang="en"[^>]*>Full name</span></label>')

    def test_the_chat_script_gets_the_note_and_its_english_original_separately(self):
        service = MemorySupabase()
        client = self.app(service, translations=_catalogue_dir(self)).test_client()
        with client.session_transaction() as session:
            session.update(user_id="review-user", user_email="t@example.invalid", access_token="t")
        cid = service.create_conversation(service, "review-user", "Chat")
        spanish = client.get(f"/chat?conversation_id={cid}", headers={"Accept-Language": "es"}).get_data(as_text=True)
        self.assertIn('const PER_MESSAGE_DISCLAIMER = "Informaci\\u00f3n general, no asesor\\u00eda legal.";', spanish)
        self.assertIn('const PER_MESSAGE_DISCLAIMER_ENGLISH = "General information, not legal advice.";', spanish)
        english = client.get(f"/chat?conversation_id={cid}").get_data(as_text=True)
        self.assertIn('const PER_MESSAGE_DISCLAIMER = "General information, not legal advice.";', english)
        self.assertIn('const PER_MESSAGE_DISCLAIMER_ENGLISH = "";', english)

    def test_translated_pages_say_so_and_link_to_the_same_page_in_english(self):
        body = self.client.get("/resources?borough=Bronx", headers={"Accept-Language": "es"}).get_data(as_text=True)
        notice = body[body.index('<p class="translation-notice"'):]
        notice = notice[:notice.index("</p>")]
        self.assertLess(body.index("<body>"), body.index('<p class="translation-notice"'))
        self.assertLess(body.index('<p class="translation-notice"'), body.index("<header"))
        self.assertIn('<a href="/resources?borough=Bronx&amp;lang=en" lang="en" hreflang="en"', notice)
        self.assertIn(">Read this page in English</a>", notice)
        self.assertIn("This page was translated by a machine.", notice)
        english = self.client.get("/resources?borough=Bronx&lang=en", headers={"Accept-Language": "es"})
        page = english.get_data(as_text=True)
        self.assertIn('<html lang="en">', page)
        self.assertNotIn("translation-notice", page)
        self.assertNotIn("Set-Cookie", english.headers, "one page in English, nothing remembered")
        self.assertIn('<html lang="es">', self.client.get("/resources", headers={"Accept-Language": "es"}).get_data(as_text=True))

    def test_the_one_page_override_only_takes_live_languages_and_keeps_the_query(self):
        with self.client.application.test_request_context("/area?street=Echo+Place&borough=Bronx&lang=es"):
            self.assertEqual(i18n.current(), "es")
            self.assertEqual(i18n.english_url(), "/area?street=Echo+Place&borough=Bronx&lang=en")
        with self.client.application.test_request_context("/learn-more?lang=klingon", headers={"Accept-Language": "fr"}):
            self.assertEqual(i18n.current(), "fr")
        with mock.patch.object(i18n, "LIVE_LANGUAGES", ("en",)), self.client.application.test_request_context("/?lang=es"):
            self.assertEqual(i18n.current(), "en")

    def test_the_menu_names_each_language_in_itself_and_marks_the_current_one(self):
        body = self.client.get("/login", headers={"Accept-Language": "es"}).get_data(as_text=True)
        self.assertIn('<form class="language-form" method="POST" action="/language">', body)
        self.assertIn('<input type="hidden" name="next" value="/login">', body)
        self.assertIn('<label for="language-choice" class="language-label">Idioma</label>', body)
        self.assertIn('<option value="es" lang="es" selected>Español</option>', body)
        self.assertIn('<option value="zh_Hant" lang="zh-Hant">繁體中文</option>', body)
        self.assertIn('<option value="en" lang="en">English</option>', body)
        self.assertIn(">Cambiar</button>", body)
        self.assertEqual(body.count("<option"), len(i18n.LANGUAGES))
        self.assertNotIn("csrf_token", body[body.index('class="language-form"'):body.index("</form>", body.index('class="language-form"'))])

    def test_messages_from_python_are_translated_with_their_values_filled_in(self):
        body = self.client.post("/login", data={"email": "", "password": ""}, headers={"Accept-Language": "es"}).get_data(as_text=True)
        self.assertIn("Escribe tu correo y tu contraseña.", body)
        with self.client.application.test_request_context("/", headers={"Accept-Language": "es"}):
            self.assertEqual(i18n.gettext("You can send a new code in %(wait)s.", wait=i18n.gettext("%(count)s seconds", count=42)),
                             "Puedes pedir otro código en 42 segundos.")
            self.assertEqual(i18n.gettext("A sentence nobody translated."), "A sentence nobody translated.")

    def test_dates_follow_the_language(self):
        day = datetime.date(2026, 10, 1)
        with self.client.application.test_request_context("/", headers={"Accept-Language": "es"}):
            self.assertEqual(i18n.format_date(day), "1 de octubre de 2026")
        with self.client.application.test_request_context("/", headers={"Accept-Language": "zh-TW"}):
            self.assertEqual(i18n.format_date(day), "2026年10月1日")

    def test_statutes_and_user_text_are_not_run_through_the_catalogue(self):
        with self.client.application.test_request_context("/", headers={"Accept-Language": "es"}):
            resources = i18n.translated(branding.HELP_RESOURCES, keys={"detail"})
        for original, shown in zip(branding.HELP_RESOURCES, resources):
            self.assertEqual((shown["name"], shown["url"], shown["contact"]), (original["name"], original["url"], original["contact"]))


class HelperTests(unittest.TestCase):
    def test_without_translation_set_up_text_comes_back_unchanged(self):
        self.assertEqual(i18n.gettext("Plain text."), "Plain text.")
        self.assertEqual(i18n.gettext("Wait %(wait)s.", wait="1 minute"), "Wait 1 minute.")
        self.assertEqual(i18n.current(), "en")
        self.assertEqual(i18n.translated({"a": ["x", {"b": "y", "n": 3}], "u": None}), {"a": ["x", {"b": "y", "n": 3}], "u": None})

    def test_only_the_named_keys_of_a_resource_are_translated(self):
        with mock.patch.object(i18n, "gettext", side_effect=lambda text, **kw: text.upper()):
            out = i18n.translated([{"name": "Agency", "detail": "free help", "url": "https://x.example"}], keys={"detail"})
        self.assertEqual(out, [{"name": "Agency", "detail": "FREE HELP", "url": "https://x.example"}])


class CatalogueTests(unittest.TestCase):
    def read(self, code):
        with open(os.path.join(_TRANSLATIONS, code, "LC_MESSAGES", "messages.po"), "rb") as handle:
            return read_po(handle)

    def test_every_planned_language_has_a_catalogue(self):
        for code in i18n.LANGUAGES:
            if code != "en":
                with self.subTest(code=code):
                    self.assertTrue(os.path.exists(os.path.join(_TRANSLATIONS, code, "LC_MESSAGES", "messages.po")))

    def test_the_template_holds_the_legal_wording_marked_for_review(self):
        with open(os.path.join(_TRANSLATIONS, "messages.pot"), "rb") as handle:
            template = read_po(handle)
        by_id = {message.id: message for message in template if message.id}
        legal = [branding.SHORT_DISCLAIMER, branding.TOP_DISCLAIMER, branding.TOP_DISCLAIMER_DETAIL,
                 branding.PER_MESSAGE_DISCLAIMER, branding.LAW_PAGE_NOTE, branding.LAW_PAGE_NOTE_DETAIL,
                 branding.PRIVACY_NOTE, password_safety.RULE_MESSAGE, password_safety.MESSAGE]
        legal += [point[key] for point in branding.FULL_DISCLAIMER_POINTS for key in ("lead", "detail")]
        legal += [resource["detail"] for resource in branding.HELP_RESOURCES]
        legal += [section["intro"] for section in branding.PRIVACY_SECTIONS]
        for text in legal:
            with self.subTest(text=text[:40]):
                self.assertIn(text, by_id)
                self.assertTrue(any("REVIEW" in comment for comment in by_id[text].auto_comments))
        for resource in branding.HELP_RESOURCES:
            self.assertNotIn(resource["name"], by_id, "official names are not translated")
            self.assertNotIn(resource["url"], by_id)

    def test_every_message_wrapped_in_routes_is_in_the_template(self):
        with open(os.path.join(_TRANSLATIONS, "messages.pot"), "rb") as handle:
            ids = {message.id for message in read_po(handle)}
        with open(os.path.join(_ROOT, "routes.py"), encoding="utf-8") as handle:
            source = handle.read()
        literals = re.findall(r'\b_\(\s*"((?:[^"\\]|\\.)+)"\s*[,)]', source)
        self.assertGreater(len(literals), 50)
        for literal in literals:
            self.assertIn(literal.replace('\\"', '"').replace("\\'", "'"), ids, literal[:50])

    def test_no_live_language_is_missing_reviewed_wording(self):
        for code in i18n.LIVE_LANGUAGES:
            if code == "en":
                continue
            for message in self.read(code):
                if message.id and any("REVIEW" in c for c in message.auto_comments):
                    self.assertTrue(message.string, f"{code}: {str(message.id)[:40]!r} is live but untranslated")
                    self.assertFalse(message.fuzzy, f"{code}: {str(message.id)[:40]!r} is still marked fuzzy")

    def test_traditional_chinese_is_defined_but_never_live_and_stays_empty(self):
        self.assertIn("zh_Hant", i18n.LANGUAGES)
        self.assertNotIn("zh_Hant", i18n.LIVE_LANGUAGES)
        self.assertEqual([m.id for m in self.read("zh_Hant") if m.id and m.string], [])

    def test_translations_keep_their_placeholders_and_hold_no_markup(self):
        """Template text is trusted as markup once translated (i18n.markup_gettext),
        so a translation may never introduce a tag, and must keep every
        %(name)s of its source or the page would fail to render."""
        for code in i18n.LANGUAGES:
            if code == "en":
                continue
            for message in self.read(code):
                if not message.id or not message.string:
                    continue
                with self.subTest(code=code, id=str(message.id)[:40]):
                    self.assertNotRegex(message.string, r"[<>]")
                    self.assertEqual(sorted(re.findall(r"%\(\w+\)s", message.string)),
                                     sorted(re.findall(r"%\(\w+\)s", message.id)))

    def test_the_source_text_holds_no_markup_either(self):
        with open(os.path.join(_TRANSLATIONS, "messages.pot"), "rb") as handle:
            for message in read_po(handle):
                if message.id:
                    self.assertNotRegex(message.id, r"<[a-zA-Z/]", str(message.id)[:60])


class NothingLeftUnwrappedTests(unittest.TestCase):
    """Every piece of literal page text goes through _() or gettext()."""

    SKIP = re.compile(r"<(style|script|svg)\b.*?</\1>|\{#.*?#\}", re.S)
    JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)
    ATTRIBUTE = re.compile(r'\b(placeholder|aria-label|title|alt|data-prompt)="([^"]*)"')

    def sources(self):
        for path in sorted(glob.glob(os.path.join(_TEMPLATES, "*.html"))):
            with open(path, encoding="utf-8") as handle:
                yield os.path.basename(path), self.SKIP.sub("", handle.read())

    def test_no_literal_text_between_tags(self):
        for name, source in self.sources():
            attributes_removed = self.ATTRIBUTE.sub("", source)
            bare = self.JINJA.sub("\x00", attributes_removed)
            text_only = re.sub(r"<[^<>]*>", "\x01", bare)
            text_only = re.sub(r"&[#A-Za-z0-9]+;", "", text_only)
            for found in re.findall(r"[A-Za-z][A-Za-z' ,.\-]{2,}", text_only):
                self.fail(f"{name}: literal text {found.strip()!r} is not wrapped for translation")

    def test_no_literal_text_in_spoken_or_shown_attributes(self):
        for name, source in self.sources():
            for attribute, value in self.ATTRIBUTE.findall(source):
                literal = self.JINJA.sub("", value)
                self.assertNotRegex(literal, r"[A-Za-z]{3}", f"{name}: {attribute}={value[:50]!r}")

    def test_script_strings_shown_to_people_come_from_the_catalogue(self):
        with open(os.path.join(_TEMPLATES, "chat.html"), encoding="utf-8") as handle:
            chat = handle.read()
        for text in ("Assistant is thinking", "Good morning", "Connection interrupted. Your message is still below; please try again.",
                     "Saved. Your next reply will use it.", "%(count)s characters left", "Review RA-81 draft %(number)s"):
            self.assertIn('{{ gettext("%s")|tojson }}' % text, chat)
        for path in glob.glob(os.path.join(_TEMPLATES, "*.html")):
            with open(path, encoding="utf-8") as handle:
                name, source = os.path.basename(path), handle.read()
            for block in re.findall(r"<script\b[^>]*>(.*?)</script>", source, re.S):
                for literal in re.findall(r'(?<!gettext\()"([A-Z][a-z]+(?: [a-z\']+){2,}[.…]?)"', block):
                    self.fail(f"{name}: script string {literal!r} is not passed through gettext")

    def test_messages_raised_in_other_modules_are_in_the_template(self):
        with open(os.path.join(_TRANSLATIONS, "messages.pot"), "rb") as handle:
            ids = {message.id for message in read_po(handle)}
        for text in ("Please enter your %(label)s.", "first name", "Choose a borough.", "Enter a street name.",
                     "Include the building number, e.g. 231 Echo Place, Bronx.", "%(label)s must be %(limit)s characters or fewer.",
                     "Review the details and check the confirmation box before downloading.", "your full name",
                     "We couldn't find that page", "Full name", "Owner or agent", "Certified mail", "Apartment status",
                     "Your name and date of birth", "Message is too long (max %(limit)s characters).", "Conversation not found."):
            self.assertIn(text, ids)

if __name__ == "__main__":
    unittest.main()
