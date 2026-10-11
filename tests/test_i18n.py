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
            self.assertNotIn('lang="en"', source, name)
            if "<html" in source:
                self.assertIn('<html lang="{{ html_lang() }}">', source, name)
                self.assertIn("{{ language_form() }}", source, name)


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
        self.assertNotIn(branding.SHORT_DISCLAIMER, spanish)
        english = self.client.get("/learn-more").get_data(as_text=True)
        self.assertIn(branding.SHORT_DISCLAIMER, english)
        self.assertNotIn(SPANISH[branding.SHORT_DISCLAIMER], english)

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

    def test_catalogues_that_are_not_live_hold_no_unreviewed_translations(self):
        for code in i18n.LANGUAGES:
            if code == "en" or code in i18n.LIVE_LANGUAGES:
                continue
            filled = [m.id for m in self.read(code) if m.id and m.string]
            self.assertEqual(filled, [], f"{code} has translations but is not live")


if __name__ == "__main__":
    unittest.main()
