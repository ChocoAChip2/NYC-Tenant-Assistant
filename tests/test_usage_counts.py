"""Usage counts: what may be recorded, what never is, and the privacy page.

Offline. The database is a stand-in that records what record_usage() would
be sent; the migration and the SQL checks are read as text. Nothing here
proves the SQL runs: that is what supabase/checks is for, on the database.
"""

import glob
import html
import io
import json
import logging
import os
import re
import unittest
from unittest import mock

# The tests that build the real app (create_app) need a secret key set
# before it is imported, as tests/test_security_hardening.py does.
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-for-usage-count-tests")

import flask

import alerting
import area_service
import branding
import building_service as bs
import law_service
import usage_metrics
import usage_routes
from rate_limit import limiter
from routes import main_bp
from tests.app_test_support import configure_test_app
from tests.review_support import MemorySupabase, ReviewAI
from tests.test_area_lookup import FakeOfficialsClient, fake_get_json
from tests.test_building_lookup import fake_network
from tests.test_law_page import HMC, FakeClient as LawClient

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TEMPLATES = os.path.join(_ROOT, "templates")
_MIGRATION = glob.glob(os.path.join(_ROOT, "supabase", "migrations", "*_usage_daily_counts.sql"))[0]
_CHECKS = os.path.join(_ROOT, "supabase", "checks", "20261011_usage_counts_checks.sql")
_REPORT = os.path.join(_ROOT, "supabase", "checks", "20261011_usage_counts_report.sql")

PERSONAL = ("231 Echo Place, Bronx", "tenant@example.com", "Echo Place", "10457", "4B", "Grand Concourse",
            "Maria Rodriguez", "My landlord won't fix the heat", "/building?address=231+Echo+Place", "x" * 200,
            "192.168.1.20", "550e8400-e29b-41d4-a716-446655440000")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class RpcRecorder:
    """Stands in for the Supabase client: remembers what record_usage was sent."""

    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def rpc(self, name, params):
        recorder = self

        class Call:
            def execute(self):
                if recorder.error:
                    raise recorder.error
                recorder.calls.append((name, json.loads(json.dumps(params))))

        return Call()


class _Case(unittest.TestCase):
    def setUp(self):
        if getattr(limiter, "_storage", None) is not None:
            limiter.reset()
        for clear in (usage_metrics.clear, bs.clear_caches, area_service.clear_caches, law_service.clear_cache):
            clear()
            self.addCleanup(clear)

    def app(self, service=None):
        app = flask.Flask(__name__, template_folder=_TEMPLATES)
        app.secret_key = "t"
        app.config.update(SUPABASE_SERVICE=service if service is not None else object(), AI_SERVICE=ReviewAI(), TESTING=True)
        app.register_blueprint(main_bp)
        configure_test_app(app)
        return app

    def counts(self):
        return usage_metrics.pending()


class AllowlistTests(_Case):
    def test_personal_data_is_rejected_for_every_event(self):
        for event in (*usage_metrics.EVENTS, *usage_metrics.PATTERN_EVENTS, "made_up_event", ""):
            for value in PERSONAL:
                with self.subTest(event=event, value=value[:30]):
                    self.assertFalse(usage_metrics.record(event, value))
        self.assertEqual(self.counts(), {})

    def test_only_listed_events_and_values_count(self):
        self.assertTrue(usage_metrics.record("page_view", "home"))
        self.assertTrue(usage_metrics.record("page_view", "home"))
        self.assertTrue(usage_metrics.record("area_borough", "Bronx"))
        self.assertTrue(usage_metrics.record("law_view", "27-2029"))
        for event, value in (("page_view", "admin"), ("page_view", ""), ("page_view", None), (None, "home"),
                             ("page_view", 7), ("page_view", ["home"]), ("area_borough", "Yonkers"),
                             ("law_view", "27-2029; drop"), ("law_view", "apt 4b"), ("resource_click", "https://evil.example"),
                             ("chip_click", "My landlord won't fix something")):
            with self.subTest(event=event, value=value):
                self.assertFalse(usage_metrics.record(event, value))
        self.assertEqual(self.counts(), {("page_view", "home"): 2, ("area_borough", "Bronx"): 1, ("law_view", "27-2029"): 1})

    def test_every_allowed_value_is_a_short_fixed_name(self):
        for event, values in usage_metrics.EVENTS.items():
            self.assertRegex(event, r"^[a-z0-9_]{1,40}$")
            self.assertTrue(values, event)
            for value in values:
                with self.subTest(event=event, value=value):
                    self.assertLessEqual(len(value), usage_metrics.MAX_NAME_LENGTH)
                    self.assertRegex(value, r"^[A-Za-z0-9_ ]+$")
        self.assertEqual(usage_metrics.EVENTS["area_borough"], frozenset(area_service.BOROUGHS))

    def test_the_recorder_has_no_way_to_take_an_identifier(self):
        import inspect

        self.assertEqual(list(inspect.signature(usage_metrics.record).parameters), ["event", "dim"])

    def test_resource_ids_come_from_the_fixed_lists(self):
        self.assertIn("housing_court_answers", usage_metrics.RESOURCE_IDS)
        self.assertIn("bronx_housing_court", usage_metrics.RESOURCE_IDS)
        self.assertEqual(usage_metrics.slug("NYC Tenant Helpline"), "nyc_tenant_helpline")
        self.assertLessEqual(max(len(r) for r in usage_metrics.RESOURCE_IDS), usage_metrics.MAX_NAME_LENGTH)

    def test_guard_kinds_match_what_the_guard_can_report(self):
        import citation_guard

        source = _read(os.path.join(_ROOT, "citation_guard.py"))
        self.assertEqual(set(re.findall(r'Violation\(\s*"([a-z_]+)"', source)), set(citation_guard.VIOLATION_KINDS))


class BufferedWriteTests(_Case):
    def test_nothing_is_written_until_enough_has_built_up(self):
        usage_metrics.record("page_view", "home")
        self.assertFalse(usage_metrics.due())
        for _ in range(usage_metrics.FLUSH_AFTER_EVENTS):
            usage_metrics.record("page_view", "home")
        self.assertTrue(usage_metrics.due())

    def test_or_until_a_minute_has_passed(self):
        usage_metrics.record("page_view", "home")
        with usage_metrics._lock:
            started = usage_metrics._state["since"]
        self.assertFalse(usage_metrics.due(now=started + usage_metrics.FLUSH_AFTER_SECONDS - 1))
        self.assertTrue(usage_metrics.due(now=started + usage_metrics.FLUSH_AFTER_SECONDS))
        usage_metrics.clear()
        self.assertFalse(usage_metrics.due(now=1e12))

    def test_one_call_carries_only_event_value_and_count(self):
        for _ in range(3):
            usage_metrics.record("page_view", "home")
        usage_metrics.record("area_borough", "Bronx")
        db = RpcRecorder()
        self.assertEqual(usage_metrics.flush(db), 2)
        self.assertEqual(len(db.calls), 1)
        name, params = db.calls[0]
        self.assertEqual(name, "record_usage")
        self.assertEqual(list(params), ["p_rows"])
        self.assertEqual(sorted(params["p_rows"], key=lambda r: r["event"]), [
            {"event": "area_borough", "dim": "Bronx", "n": 1}, {"event": "page_view", "dim": "home", "n": 3}])
        self.assertEqual({key for row in params["p_rows"] for key in row}, {"event", "dim", "n"})
        self.assertEqual(self.counts(), {})
        self.assertEqual(usage_metrics.flush(db), 0)

    def test_a_failed_write_drops_the_counts_and_backs_off(self):
        usage_metrics.record("page_view", "home")
        with self.assertLogs("usage_metrics", level="WARNING") as logs:
            self.assertEqual(usage_metrics.flush(RpcRecorder(error=RuntimeError("function record_usage does not exist"))), 0)
        self.assertNotIn("does not exist", "\n".join(logs.output))
        self.assertEqual(self.counts(), {})
        for _ in range(usage_metrics.FLUSH_AFTER_EVENTS):
            usage_metrics.record("page_view", "home")
        self.assertFalse(usage_metrics.due(), "no retry straight after a failure")
        with usage_metrics._lock:
            failed = usage_metrics._state["failed_at"]
        self.assertTrue(usage_metrics.due(now=failed + usage_metrics.RETRY_AFTER_SECONDS + 1))

    def test_no_client_means_no_write_and_no_error(self):
        usage_metrics.record("page_view", "home")
        self.assertEqual(usage_metrics.flush(None), 0)

    def test_a_page_never_fails_because_counting_did(self):
        with mock.patch.object(usage_routes, "count_request", side_effect=RuntimeError("boom")), \
             self.assertLogs("usage_routes", level="WARNING"):
            self.assertEqual(self.app().test_client().get("/learn-more").status_code, 200)

    def test_the_hook_writes_in_the_background_when_due(self):
        db = RpcRecorder()
        service = type("Service", (), {"client": db})()
        with mock.patch.object(usage_metrics, "FLUSH_AFTER_EVENTS", 1), \
             mock.patch.object(usage_metrics.threading, "Thread") as thread:
            self.app(service).test_client().get("/learn-more")
        thread.assert_called_once()
        self.assertEqual(thread.call_args.kwargs["args"], (db,))
        self.assertTrue(thread.call_args.kwargs["daemon"])


class NothingPersonalLeavesTheRequestTests(_Case):
    SECRETS = ("ECHO", "231", "4B", "10457", "CONCOURSE", "GRAND")

    def assertClean(self, text, where):
        for secret in self.SECRETS:
            self.assertNotIn(secret, text.upper(), f"{secret!r} found in {where}")

    def test_building_and_street_searches_record_no_address(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setLevel(logging.DEBUG)
        webhook = alerting.WebhookAlertHandler("https://hooks.invalid/x")
        root = logging.getLogger()
        previous = root.level
        root.setLevel(logging.DEBUG)
        for h in (handler, webhook):
            root.addHandler(h)
            self.addCleanup(root.removeHandler, h)
        self.addCleanup(root.setLevel, previous)
        sent = []
        db = RpcRecorder()
        service = type("Service", (), {"client": FakeOfficialsClient()})()
        client = self.app(service).test_client()
        patcher, _ = fake_network()
        getter, _ = fake_get_json()

        with mock.patch("alerting.urllib.request.urlopen", side_effect=lambda request, timeout=None: sent.append(request.data.decode())):
            with patcher:
                self.assertEqual(client.get("/building?address=231+Echo+Place%2C+Bronx+10457&apt=4B").status_code, 200)
                self.assertEqual(client.get("/?address=231+Echo+Place%2C+Bronx&apt=4B").status_code, 200)
            with mock.patch.object(area_service, "_get_json", getter):
                self.assertEqual(client.get("/area?street=Echo+Place&borough=Bronx&zip=10457&cross=Grand+Concourse").status_code, 200)
                self.assertEqual(client.get("/?street=Echo+Place&borough=Bronx").status_code, 200)
            with mock.patch("routes.building_service.lookup", side_effect=RuntimeError("upstream exploded")):
                self.assertEqual(client.get("/building?address=231+Echo+Place%2C+Bronx&apt=4B").status_code, 500)
            recorded = json.dumps(sorted(map(list, self.counts().items())))
            usage_metrics.flush(db)

        self.assertIn("building_lookup", recorded)
        self.assertIn("area_search", recorded)
        self.assertClean(recorded.replace("Bronx", ""), "the recorded counts")
        self.assertClean(json.dumps(db.calls), "what was sent to the database")
        self.assertClean(stream.getvalue(), "the log")
        self.assertTrue(sent, "the unexpected failure should have reached the alert webhook")
        self.assertClean("\n".join(sent), "the alert webhook payload")

    def test_what_is_recorded_for_those_searches(self):
        client = self.app(type("Service", (), {"client": FakeOfficialsClient()})()).test_client()
        patcher, _ = fake_network()
        getter, _ = fake_get_json()
        with patcher:
            client.get("/building?address=231+Echo+Place%2C+Bronx&apt=4B")
            client.get("/building?address=Echo+Place")
        with patcher:
            bs.clear_caches()
        nothing, _ = fake_network(geocode={"features": []})
        with nothing:
            client.get("/building?address=99999+Nowhere+Street")
        with mock.patch.object(area_service, "_get_json", getter):
            client.get("/area?street=Echo+Place&borough=Bronx&zip=10457")
            client.get("/area?street=Asdfgh+Street&borough=Queens")
            client.get("/area?street=Echo+Place&borough=Narnia")
        counts = self.counts()
        self.assertEqual(counts[("building_lookup", "found")], 1)
        self.assertEqual(counts[("building_lookup", "invalid")], 1)
        self.assertEqual(counts[("building_lookup", "not_found")], 1)
        self.assertEqual(counts[("area_search", "found")], 1)
        self.assertEqual(counts[("area_search", "no_match")], 1)
        self.assertEqual(counts[("area_search", "invalid")], 1)
        self.assertEqual(counts[("area_borough", "Bronx")], 1)
        self.assertEqual(counts[("area_borough", "Queens")], 1)
        self.assertEqual(counts[("area_zip_used", "yes")], 1)
        self.assertEqual(counts[("area_zip_used", "no")], 2)
        self.assertEqual(counts[("page_view", "building")], 1)
        self.assertEqual(counts[("page_view", "area")], 1)
        self.assertEqual(counts[("error_status", "404")], 2)
        self.assertEqual(counts[("error_status", "400")], 2)
        self.assertNotIn(("area_borough", "Narnia"), counts)


class RequestCountingTests(_Case):
    def test_a_page_view_is_a_route_name_and_four_context_values(self):
        self.app().test_client().get("/learn-more?utm_source=x&address=231+Echo+Place", headers={
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148",
            "Accept-Language": "es-US,es;q=0.9,en;q=0.8", "Referer": "https://www.google.com/search?q=231+echo+place"})
        self.assertEqual(self.counts(), {
            ("page_view", "learn_more"): 1, ("view_device", "mobile"): 1, ("view_language", "es"): 1,
            ("view_referrer", "search"): 1, ("view_signed_in", "no"): 1})

    def test_referrers_are_categories_never_addresses(self):
        client = self.app().test_client()
        cases = {None: "direct", "http://localhost/chat": "internal", "https://duckduckgo.com/?q=x": "search",
                 "https://m.facebook.com/groups/tenants": "social", "https://t.co/abc": "social",
                 "https://tenants-union.example/links": "other"}
        for referer, expected in cases.items():
            usage_metrics.clear()
            client.get("/learn-more", headers={"Referer": referer} if referer else {})
            with self.subTest(referer=referer):
                self.assertEqual(self.counts().get(("view_referrer", expected)), 1)

    def test_language_and_device_fall_back_to_fixed_values(self):
        client = self.app().test_client()
        client.get("/learn-more", headers={"Accept-Language": "tlh-Qaak, <script>", "User-Agent": "curl/8"})
        counts = self.counts()
        self.assertEqual(counts[("view_language", "other")], 1)
        self.assertEqual(counts[("view_device", "desktop")], 1)

    def test_global_privacy_control_means_not_counted_at_all(self):
        client = self.app().test_client()
        patcher, _ = fake_network()
        with patcher:
            client.get("/building?address=231+Echo+Place%2C+Bronx", headers={"Sec-GPC": "1"})
        client.get("/learn-more", headers={"Sec-GPC": "1"})
        client.post("/usage/click", data={"id": "housing_court_answers"},
                    headers={"Sec-GPC": "1", "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(self.counts(), {})

    def test_law_pages_count_the_section_not_the_reader(self):
        client = self.app(type("Service", (), {"client": LawClient(HMC.values())})()).test_client()
        client.get("/law/27-2029")
        client.get("/law/27-9999")
        client.get("/law")
        counts = self.counts()
        self.assertEqual(counts[("law_view", "27-2029")], 1)
        self.assertNotIn(("law_view", "27-9999"), counts)
        self.assertEqual(counts[("page_view", "law")], 1)
        self.assertEqual(counts[("page_view", "law_index")], 1)
        self.assertEqual(counts[("error_route", "law")], 1)

    def test_chat_counts_that_a_message_was_sent_and_nothing_about_it(self):
        service = MemorySupabase()
        client = self.app(service).test_client()
        with client.session_transaction() as session:
            session.update(user_id="review-user", user_email="review@example.invalid", access_token="t")
        cid = client.post("/conversations", data={"title": "Repairs and heat", "chip": "repairs"}).location.split("conversation_id=")[1]
        client.post("/chat/message", json={"conversation_id": cid, "content": "My landlord at 231 Echo Place won't fix the heat"})
        client.post("/chat/message", json={"conversation_id": cid, "content": "Please fail once"})
        client.post(f"/conversations/{cid}/archive")
        client.post(f"/conversations/{cid}/delete")
        client.get("/chat")
        counts = self.counts()
        self.assertEqual(counts[("chat", "conversation_created")], 1)
        self.assertEqual(counts[("chip_click", "repairs")], 1)
        self.assertEqual(counts[("chat", "message_sent")], 1)
        self.assertEqual(counts[("ai_error", "unavailable")], 1)
        self.assertEqual(counts[("chat", "conversation_archived")], 1)
        self.assertEqual(counts[("chat", "conversation_deleted")], 1)
        self.assertEqual(counts[("view_signed_in", "yes")], 1)
        self.assertNotIn(("chat", "reply_with_citations"), counts)
        recorded = json.dumps(sorted(map(list, counts.items())))
        for secret in ("Echo", "landlord", "heat", cid, "review-user", "review@example.invalid"):
            self.assertNotIn(secret, recorded.replace("Repairs and heat", ""))

    def test_a_reply_with_sources_is_counted_as_cited(self):
        service = MemorySupabase()
        client = self.app(service).test_client()
        with client.session_transaction() as session:
            session.update(user_id="review-user", user_email="review@example.invalid", access_token="t")
        cid = client.post("/conversations").location.split("conversation_id=")[1]
        with mock.patch("routes._ground_reply", return_value=("Heat must be provided.", [{"label": "§ 27-2029"}])):
            client.post("/chat/message", json={"conversation_id": cid, "content": "heat?"})
        self.assertEqual(self.counts()[("chat", "reply_with_citations")], 1)

    def test_link_chips_and_unknown_chips(self):
        client = self.app().test_client()
        getter, _ = fake_get_json()
        with mock.patch.object(area_service, "_get_json", getter):
            client.get("/?chip=look_up_street")
            client.get("/?chip=231+Echo+Place")
        client.get("/resources?chip=free_help")
        counts = self.counts()
        self.assertEqual(counts[("chip_click", "look_up_street")], 1)
        self.assertEqual(counts[("chip_click", "free_help")], 1)
        self.assertEqual(sum(n for (event, _), n in counts.items() if event == "chip_click"), 2)

    def test_sign_up_and_login_steps(self):
        class Auth(MemorySupabase):
            def sign_up(self, email, password, email_redirect_to=None, metadata=None):
                return True

            def sign_in(self, email, password):
                raise RuntimeError("Invalid login credentials")

        client = self.app(Auth()).test_client()
        client.get("/signup")
        with mock.patch("routes.password_safety.is_breached", return_value=False):
            client.post("/signup", data={"email": "t@example.invalid", "password": "Str0ng!pass",
                                         "first_name": "Ana", "last_name": "Rivera", "date_of_birth": "1990-05-17"})
        with self.assertLogs("routes", level="WARNING"):
            client.post("/login", data={"email": "t@example.invalid", "password": "nope"})
        counts = self.counts()
        self.assertEqual(counts[("signup", "view")], 1)
        self.assertEqual(counts[("signup", "success")], 1)
        self.assertEqual(counts[("login", "failure")], 1)
        self.assertNotIn("example.invalid", json.dumps(sorted(map(list, counts.items()))))

    def test_every_counted_page_name_is_a_real_route(self):
        app = self.app()
        endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}
        self.assertLessEqual(set(usage_metrics.PAGES), endpoints)


class ClickEndpointTests(_Case):
    def post(self, client, resource_id, **headers):
        return client.post("/usage/click", data={"id": resource_id}, headers=headers)

    def test_a_click_from_our_own_page_counts_one_fixed_id(self):
        client = self.app().test_client()
        response = self.post(client, "housing_court_answers", **{"Sec-Fetch-Site": "same-origin"})
        self.assertEqual((response.status_code, response.data), (204, b""))
        self.assertEqual(self.post(client, "housing_court_answers", Origin="http://localhost").status_code, 204)
        self.assertEqual(self.counts(), {("resource_click", "housing_court_answers"): 2})

    def test_a_request_from_another_site_is_ignored(self):
        client = self.app().test_client()
        for headers in ({"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}, {"Origin": "https://evil.example"}, {}):
            with self.subTest(headers=headers):
                self.assertEqual(self.post(client, "housing_court_answers", **headers).status_code, 204)
        self.assertEqual(self.counts(), {})

    def test_anything_but_a_listed_id_is_ignored(self):
        client = self.app().test_client()
        for value in ("https://evil.example", "231 Echo Place", "x" * 500, ""):
            self.post(client, value, **{"Sec-Fetch-Site": "same-origin"})
        self.assertEqual(self.counts(), {})

    def test_the_resources_page_names_its_links_and_needs_no_token(self):
        body = self.app().test_client().get("/resources?borough=Bronx").get_data(as_text=True)
        ids = set(re.findall(r'data-usage-resource="([^"]+)"', body))
        self.assertIn("bronx_housing_court", ids)
        self.assertIn("housing_court_answers", ids)
        self.assertLessEqual(ids, usage_metrics.RESOURCE_IDS)
        self.assertIn("navigator.sendBeacon", body)
        self.assertIn("navigator.globalPrivacyControl", body)
        self.assertNotIn("csrf_token", body)

    def test_the_real_app_accepts_the_click_without_a_token_and_nothing_else(self):
        from app import create_app

        app = create_app()
        client = app.test_client()
        response = client.post("/usage/click", data={"id": "housing_court_answers"}, headers={"Sec-Fetch-Site": "same-origin"})
        self.assertEqual(response.status_code, 204)
        self.assertNotIn("Set-Cookie", response.headers)
        self.assertEqual(self.counts().get(("resource_click", "housing_court_answers")), 1)
        self.assertEqual(client.post("/logout").status_code, 400, "every other POST still needs its token")


class NoNewCookiesOrScriptsTests(_Case):
    def test_public_pages_set_no_cookie_for_a_visitor_who_is_not_signed_in(self):
        from app import create_app

        client = create_app().test_client()
        for path in ("/privacy", "/resources", "/resources?chip=free_help", "/learn-more"):
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("Set-Cookie", response.headers)

    def test_the_only_cookie_the_app_ever_sets_is_the_session_cookie(self):
        from app import create_app

        app = create_app()
        client = app.test_client()
        names = set()
        for path in ("/login", "/signup", "/privacy", "/", "/chat"):
            for header in client.get(path).headers.getlist("Set-Cookie"):
                names.add(header.split("=", 1)[0])
        self.assertLessEqual(names, {app.config.get("SESSION_COOKIE_NAME", "session")})

    def test_no_template_loads_anything_from_another_site_or_stores_an_id(self):
        for path in glob.glob(os.path.join(_TEMPLATES, "*.html")):
            source = _read(path)
            name = os.path.basename(path)
            with self.subTest(template=name):
                self.assertIsNone(re.search(r"<script[^>]+\bsrc\s*=", source), "no external script files")
                self.assertIsNone(re.search(r"<(?:img|iframe|link)[^>]+(?:src|href)\s*=\s*[\"']https?://", source))
                self.assertNotIn("document.cookie", source)
                for stored in re.findall(r"(?:localStorage|sessionStorage)\.setItem\(\s*[\"']([^\"']+)", source):
                    self.assertIn(stored, {"theme", "pendingChatPrompt", "pendingChatTitle", "pendingChatArmedAt"}, name)
                self.assertNotIn("fingerprint", source.lower())


class PrivacyPageTests(_Case):
    def test_it_is_public_and_says_what_branding_says(self):
        response = self.app().test_client().get("/privacy")
        self.assertEqual(response.status_code, 200)
        body = html.unescape(response.get_data(as_text=True))
        self.assertIn(branding.PRIVACY_LEDE, body)
        for section in branding.PRIVACY_SECTIONS:
            self.assertIn(section["title"], body)
            self.assertIn(section["intro"], body)
            for point in section["points"]:
                self.assertIn(point, body)
        self.assertIn(branding.PRIVACY_ACCOUNT_TEXT, body)
        self.assertIn(branding.SHORT_DISCLAIMER, body)

    def test_it_covers_what_is_counted_what_never_is_retention_and_the_opt_out(self):
        titles = [s["title"] for s in branding.PRIVACY_SECTIONS]
        self.assertEqual(titles, ["What we count", "What these counts never include", "How long we keep them", "Your choice"])
        text = json.dumps(branding.PRIVACY_SECTIONS)
        self.assertIn(f"deleted after {usage_metrics.RETENTION_MONTHS} months", text)
        self.assertIn(f"any total under {usage_metrics.SMALL_COUNT} is hidden", text)
        self.assertIn("Global Privacy Control", text)
        for never in ("name", "email", "address", "apartment", "ZIP", "IP address", "cookie", "chat messages"):
            self.assertIn(never, branding.PRIVACY_SECTIONS[1]["intro"] + " ".join(branding.PRIVACY_SECTIONS[1]["points"]))

    def test_the_wording_lives_in_branding_and_names_no_vendor(self):
        template = _read(os.path.join(_TEMPLATES, "privacy.html"))
        text = json.dumps([branding.PRIVACY_SECTIONS, branding.PRIVACY_LEDE, branding.PRIVACY_ACCOUNT_TEXT]).lower()
        for section in branding.PRIVACY_SECTIONS:
            self.assertNotIn(section["intro"], template)
        for vendor in ("supabase", "render", "gemini", "google", "postgres", "flask", "database", "cron"):
            self.assertNotIn(vendor, text)

    def test_it_is_linked_from_every_footer_and_from_sign_up(self):
        client = self.app().test_client()
        for path in ("/learn-more", "/login", "/signup", "/resources", "/forgot-password", "/reset-password", "/privacy"):
            with self.subTest(path=path):
                body = client.get(path).get_data(as_text=True)
                target = "/learn-more" if path == "/privacy" else "/privacy"
                self.assertIn(f'href="{target}"', body)
        self.assertIn('<a href="/privacy">Privacy</a>', client.get("/signup").get_data(as_text=True))
        for path in glob.glob(os.path.join(_TEMPLATES, "*.html")):
            source = _read(path)
            if "{{ short_disclaimer }}" in source and os.path.basename(path) != "privacy.html":
                self.assertIn("url_for('main.privacy')", source, os.path.basename(path))

    def test_a_visit_to_it_is_counted_like_any_page(self):
        self.app().test_client().get("/privacy")
        self.assertEqual(self.counts()[("page_view", "privacy")], 1)


class MigrationTextTests(unittest.TestCase):
    """What the migration says, read as text. Whether it runs is for the
    database: supabase/checks/20261011_usage_counts_checks.sql."""

    sql = _read(_MIGRATION)

    def test_the_table_has_four_columns_and_none_can_hold_an_identifier(self):
        body = re.search(r"create table if not exists public\.usage_daily \((.*?)\n\);", self.sql, re.S).group(1)
        columns = [line.strip().split()[0] for line in body.splitlines() if line.strip() and not line.strip().startswith("primary key")]
        self.assertEqual(columns, ["day", "event", "dim", "n"])
        self.assertIn("day date not null", body)
        self.assertIn("n bigint not null", body)
        self.assertIn(f"char_length(dim) <= {usage_metrics.MAX_NAME_LENGTH}", body)
        for forbidden in ("user", "session", "visitor", "ip", "agent", "url", "uuid", "inet", "jsonb", "varchar", "path", "hash"):
            self.assertIsNone(re.search(rf"\b{forbidden}\b", body), forbidden)

    def test_app_roles_can_run_the_function_and_touch_nothing_else(self):
        self.assertIn("alter table public.usage_daily enable row level security;", self.sql)
        self.assertIn("alter table public.usage_allowed enable row level security;", self.sql)
        self.assertIn("revoke all on table public.usage_daily from public, anon, authenticated;", self.sql)
        self.assertIn("revoke all on table public.usage_allowed from public, anon, authenticated;", self.sql)
        self.assertNotIn("create policy", self.sql.lower())
        grants = re.findall(r"^grant .*;$", self.sql, re.M)
        self.assertEqual(grants, ["grant execute on function public.record_usage(jsonb) to anon, authenticated;"])
        self.assertIn("revoke all on function public.record_usage(jsonb) from public;", self.sql)

    def test_the_function_is_narrow(self):
        function = self.sql[self.sql.index("create or replace function public.record_usage"):self.sql.index("comment on function")]
        self.assertIn("security definer", function)
        self.assertIn("set search_path = ''", function)
        self.assertIn("from public.usage_allowed a where a.event = v_event and a.dim = v_dim", function)
        self.assertIn(f"jsonb_array_length(p_rows) > {usage_metrics.MAX_ROWS_PER_WRITE}", function)
        self.assertIn("least(v_amount, 1000)", function)
        self.assertIn("(now() at time zone 'America/New_York')::date", function)
        self.assertNotIn("execute ", function.lower().replace("grant execute", ""), "no dynamic SQL")
        for table in re.findall(r"\b(?:from|into|update|join)\s+([a-z_.]+)", function):
            if "." in table:
                self.assertIn(table, {"public.usage_allowed", "public.usage_daily"})

    def test_the_sql_allowlist_is_exactly_the_python_one(self):
        block = self.sql[self.sql.index("insert into public.usage_allowed (event, dim) values"):self.sql.index("create or replace function")]
        in_sql = set(re.findall(r"\('([^']+)', '([^']*)'\)", block))
        in_python = {(event, dim) for event, dims in usage_metrics.EVENTS.items() for dim in dims}
        self.assertEqual(in_sql, in_python)
        self.assertEqual(set(usage_metrics.PATTERN_EVENTS), {"law_view"})
        pattern = re.search(r"v_event = 'law_view'\s+and v_dim ~ '([^']+)'", self.sql).group(1)
        for good in ("27-2029", "26-504.1", "27-2056.6.1", "8-102a", "235-b", "711", "7-108"):
            self.assertTrue(law_service.is_valid_citation(good))
            self.assertIsNotNone(re.fullmatch(pattern, good), good)
        for bad in ("apt 4b", "27-2029; drop", "231 echo place", "x" * 41, ""):
            self.assertIsNone(re.fullmatch(pattern, bad), bad)

    def test_old_counts_are_purged_daily(self):
        self.assertIn("'purge-usage-daily'", self.sql)
        self.assertRegex(self.sql, r"cron\.schedule\(\s*'purge-usage-daily',\s*'\d+ \d+ \* \* \*'")
        self.assertIn(f"delete from public.usage_daily where day < (current_date - interval '{usage_metrics.RETENTION_MONTHS} months');", self.sql)

    def test_the_database_checks_cover_the_same_promises(self):
        checks = _read(_CHECKS)
        self.assertTrue(checks.rstrip().endswith("rollback;"))
        self.assertIn("begin;", checks)
        for needle in ("day:date,event:text,dim:text,n:bigint", "231 Echo Place, Bronx", "tenant@example.com", "x" * 200,
                       "purge-usage-daily", "interval '13 months'", "has_function_privilege('anon'", "role_table_grants"):
            self.assertIn(needle, checks)

    def test_report_queries_hide_small_counts(self):
        report = _read(_REPORT)
        statements = [s for s in report.split(";") if re.search(r"^\s*(select|with)\b", s, re.M | re.I)]
        self.assertEqual(len(statements), 3)
        for statement in statements:
            self.assertRegex(statement, rf">= {usage_metrics.SMALL_COUNT}\b")
        self.assertNotIn("select *", report.lower())


if __name__ == "__main__":
    unittest.main()
