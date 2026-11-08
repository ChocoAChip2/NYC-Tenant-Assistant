"""The public domain's coming-soon page and launch list (coming_soon.py).

Owner, 2026-11-01: until launch, visitors to sidekicktidbit.com see what the
site will do and can leave an email address; the team keeps working on the
Render address, which search engines are asked not to index.
"""

import os
import unittest
from unittest import mock

import flask

import coming_soon
import crypto_service
from routes import main_bp
from tests.app_test_support import configure_test_app, reset_rate_limits

_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
DOMAIN = "https://www.sidekicktidbit.com"
TEAM = "https://tenantrightsandformsassistant.onrender.com"
KEY_ENV = {"DATA_ENCRYPTION_KEYS": "k1:" + "A" * 43 + "=", "DATA_ENCRYPTION_ACTIVE_KEY_ID": "k1",
           "COMING_SOON": "1", "SITE_LAUNCHED": ""}


class FakeRpc:
    def __init__(self, calls, fail):
        self.calls, self.fail = calls, fail

    def execute(self):
        if self.fail:
            raise RuntimeError("db down")


class FakeClient:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def rpc(self, name, params):
        self.calls.append((name, params))
        return FakeRpc(self.calls, self.fail)


class FakeSupabase:
    def __init__(self, fail=False):
        self.client = FakeClient(fail)

    def build_user_scoped_client(self, token):
        return object()

    def list_conversations(self, *a, **k):
        return []


class HostTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"COMING_SOON": "1", "SITE_LAUNCHED": ""})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_site_is_open_unless_coming_soon_is_set(self):
        with mock.patch.dict(os.environ, {"COMING_SOON": ""}):
            self.assertFalse(coming_soon.gated("sidekicktidbit.com"))
            self.assertTrue(coming_soon.hide_from_search("sidekicktidbit.com"))
        with mock.patch.dict(os.environ, {"COMING_SOON": "", "SITE_LAUNCHED": "1"}):
            self.assertFalse(coming_soon.hide_from_search("sidekicktidbit.com"))
            self.assertTrue(coming_soon.hide_from_search("tenantrightsandformsassistant.onrender.com"))

    def test_only_the_public_domain_is_gated(self):
        for host in ("sidekicktidbit.com", "www.sidekicktidbit.com", "WWW.SideKickTidbit.com:443", "sidekicktidbit.com."):
            with self.subTest(host=host):
                self.assertTrue(coming_soon.gated(host))
        for host in ("tenantrightsandformsassistant.onrender.com", "localhost:5000", "evil-sidekicktidbit.com", ""):
            with self.subTest(host=host):
                self.assertFalse(coming_soon.gated(host))

    def test_launching_opens_the_domain(self):
        with mock.patch.dict(os.environ, {"SITE_LAUNCHED": "1"}):
            self.assertFalse(coming_soon.gated("www.sidekicktidbit.com"))

    def test_emails_are_checked_and_normalised(self):
        self.assertEqual(coming_soon.clean_email("  Ana@Example.ORG "), "ana@example.org")
        for bad in ("", "ana", "ana@", "@example.com", "ana@example", "a b@example.com", "x" * 250 + "@e.com"):
            with self.subTest(bad=bad):
                self.assertIsNone(coming_soon.clean_email(bad))


class _Case(unittest.TestCase):
    def setUp(self):
        reset_rate_limits()
        patcher = mock.patch.dict(os.environ, KEY_ENV)
        patcher.start()
        crypto_service.reload_keys()
        self.addCleanup(crypto_service.reload_keys)  # runs last, once the real environment is back
        self.addCleanup(patcher.stop)
        self.service = FakeSupabase()
        app = flask.Flask(__name__, template_folder=_TEMPLATES_DIR)
        app.secret_key = "test"
        app.config["SUPABASE_SERVICE"] = self.service
        app.register_blueprint(main_bp)
        configure_test_app(app)
        self.client = app.test_client()

    def get(self, path, base=DOMAIN, **kw):
        return self.client.get(path, base_url=base, **kw)

    def post(self, path, data, base=DOMAIN):
        return self.client.post(path, data=data, base_url=base)


class GateTests(_Case):
    def test_the_domain_shows_the_coming_soon_page(self):
        response = self.get("/")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Welcome to SideKick Tidbit", body)
        self.assertIn("still in the works", body)
        self.assertIn("Here is what we do", body)
        self.assertIn('name="email"', body)
        self.assertNotIn("<!--", body)

    def test_every_other_page_on_the_domain_goes_home(self):
        for path in ("/area?street=echo+pl&borough=Bronx", "/building?address=1+main", "/chat", "/login", "/signup", "/resources"):
            with self.subTest(path=path):
                response = self.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.headers["Location"].endswith("/"))

    def test_privacy_still_opens_on_the_domain(self):
        response = self.get("/privacy")
        self.assertEqual(response.status_code, 200)
        self.assertIn("The launch list", response.get_data(as_text=True))

    def test_the_team_address_is_the_real_site_and_unindexed(self):
        response = self.get("/", base=TEAM)
        self.assertNotIn("Welcome to SideKick Tidbit", response.get_data(as_text=True))
        self.assertEqual(response.headers.get("X-Robots-Tag"), "noindex, nofollow")
        self.assertEqual(self.get("/").headers.get("X-Robots-Tag"), "noindex, nofollow")

    def test_without_coming_soon_the_domain_is_the_real_site_but_unindexed(self):
        with mock.patch.dict(os.environ, {"COMING_SOON": ""}):
            response = self.get("/")
            body = response.get_data(as_text=True)
            self.assertNotIn("Here is what we do", body)
            self.assertEqual(response.headers.get("X-Robots-Tag"), "noindex, nofollow")
            self.assertEqual(self.get("/login").status_code, 200)

    def test_after_launch_the_domain_is_the_real_site(self):
        with mock.patch.dict(os.environ, {"SITE_LAUNCHED": "true"}):
            body = self.get("/").get_data(as_text=True)
        self.assertNotIn("Welcome to SideKick Tidbit", body)


class LaunchListTests(_Case):
    def test_an_address_is_stored_encrypted_with_a_keyed_hash(self):
        response = self.post("/launch-list", {"email": " Ana@Example.org "})
        self.assertEqual(response.status_code, 200)
        self.assertIn("We'll email you when the site opens", response.get_data(as_text=True))
        name, params = self.service.client.calls[0]
        self.assertEqual(name, "join_launch_list")
        self.assertTrue(params["p_email"].startswith("enc:v"))
        self.assertNotIn("ana", params["p_email"].lower())
        self.assertEqual(crypto_service.decrypt(params["p_email"]), "ana@example.org")
        self.assertRegex(params["p_email_key"], r"^[0-9a-f]{64}$")
        self.assertEqual(params["p_email_key"], coming_soon.email_key("ana@example.org"))
        self.assertNotEqual(params["p_email_key"], __import__("hashlib").sha256(b"ana@example.org").hexdigest())

    def test_a_bad_address_is_explained_and_kept_in_the_box(self):
        response = self.post("/launch-list", {"email": "ana@example"})
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Enter an email address", body)
        self.assertIn('value="ana@example"', body)
        self.assertEqual(self.service.client.calls, [])

    def test_the_bot_trap_stores_nothing(self):
        response = self.post("/launch-list", {"email": "ana@example.org", "website": "http://spam"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.service.client.calls, [])

    def test_a_database_failure_asks_to_try_again(self):
        self.service.client.fail = True
        response = self.post("/launch-list", {"email": "ana@example.org"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("try again", response.get_data(as_text=True))

    def test_without_encryption_nothing_is_stored(self):
        with mock.patch.dict(os.environ, {"DATA_ENCRYPTION_KEYS": "", "DATA_ENCRYPTION_ACTIVE_KEY_ID": ""}):
            crypto_service.reload_keys()
            response = self.post("/launch-list", {"email": "ana@example.org"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.service.client.calls, [])


PREVIEW_KEY = "team-key-for-tests-0123456789"


class TeamPreviewTests(_Case):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"TEAM_PREVIEW_KEY": PREVIEW_KEY})
        patcher.start()
        self.addCleanup(patcher.stop)

    def enter(self, key=PREVIEW_KEY):
        return self.post("/team-preview", {"key": key})

    def test_the_right_key_opens_the_real_site_on_the_domain(self):
        response = self.enter()
        self.assertEqual(response.status_code, 302)
        cookie = response.headers["Set-Cookie"]
        self.assertIn("team_preview=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertNotIn(PREVIEW_KEY, cookie)
        body = self.get("/").get_data(as_text=True)
        self.assertNotIn("Here is what we do", body)
        self.assertEqual(self.get("/login").status_code, 200)

    def test_a_wrong_key_is_refused(self):
        response = self.enter("wrong-key-wrong-key-wrong")
        self.assertEqual(response.status_code, 403)
        self.assertIn("That key didn&#39;t work", response.get_data(as_text=True))
        self.assertIn("Here is what we do", self.get("/").get_data(as_text=True))

    def test_signing_out_keeps_the_preview_and_leaving_ends_it(self):
        self.enter()
        self.post("/logout", {})
        self.assertNotIn("Here is what we do", self.get("/").get_data(as_text=True))
        self.get("/team-preview/leave")
        self.assertIn("Here is what we do", self.get("/").get_data(as_text=True))

    def test_changing_the_key_in_render_ends_every_preview(self):
        self.enter()
        with mock.patch.dict(os.environ, {"TEAM_PREVIEW_KEY": "a-different-key-0123456789"}):
            self.assertIn("Here is what we do", self.get("/").get_data(as_text=True))
        with mock.patch.dict(os.environ, {"TEAM_PREVIEW_KEY": ""}):
            self.assertIn("Here is what we do", self.get("/").get_data(as_text=True))

    def test_a_forged_cookie_does_nothing(self):
        self.client.set_cookie(coming_soon.PREVIEW_COOKIE, "anything", domain="www.sidekicktidbit.com")
        self.assertIn("Here is what we do", self.get("/").get_data(as_text=True))

    def test_off_when_no_key_or_a_short_key_is_set(self):
        for value in ("", "short"):
            with self.subTest(value=value), mock.patch.dict(os.environ, {"TEAM_PREVIEW_KEY": value}):
                self.assertEqual(self.get("/team-preview").status_code, 404)
                self.assertEqual(self.enter(value or "x").status_code, 404)

    def test_the_domain_is_never_cached_between_visitors(self):
        response = self.get("/")
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertIn("Cookie", response.headers.get("Vary", ""))


class MigrationTests(unittest.TestCase):
    def test_the_table_is_closed_except_through_the_function(self):
        folder = os.path.join(os.path.dirname(_TEMPLATES_DIR), "supabase", "migrations")
        sql = next(open(os.path.join(folder, n)).read() for n in os.listdir(folder) if n.endswith("_launch_list.sql"))
        self.assertIn("enable row level security", sql)
        self.assertIn("revoke all on table public.launch_list from public, anon, authenticated", sql)
        self.assertIn("security definer", sql)
        self.assertIn("set search_path = ''", sql)
        self.assertIn("p_email not like 'enc:v%'", sql)
        self.assertIn("on conflict (email_key) do nothing", sql)


if __name__ == "__main__":
    unittest.main()
