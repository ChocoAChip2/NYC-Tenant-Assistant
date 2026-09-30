"""The front door is the building lookup; signup lives at /signup.

The lookup needs no account and is the part of the site a general chatbot
can't do, so it is what a first-time visitor sees at /. These tests pin
that, keep old links working (/building, a signup form left open from
before the move), and check that confirmation emails land on /login.
"""

import inspect
import unittest

import routes
import supabase_service as ss
from tests.test_building_lookup import _CacheIsolation, _client, fake_network
from tests.test_signup_route import _build_test_app


class HomeIsTheLookupTests(_CacheIsolation):
    def test_root_is_the_building_lookup(self):
        response = _client().get("/")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('name="address"', body)
        self.assertIn("no account needed", body)

    def test_a_lookup_works_at_the_root(self):
        patcher, _ = fake_network()
        with patcher:
            body = _client().get("/?address=231+echo+pl+bx").get_data(as_text=True)
        self.assertIn("231 ECHO PLACE, Bronx, NY, USA", body)

    def test_old_building_links_still_work(self):
        patcher, _ = fake_network()
        with patcher:
            response = _client().get("/building?address=231+echo+pl+bx")
        self.assertEqual(response.status_code, 200)
        self.assertIn("231 ECHO PLACE", response.get_data(as_text=True))

    def test_lookup_form_still_submits_to_building(self):
        # Shared lookup links keep their /building?address=... shape.
        self.assertIn('action="/building"', _client().get("/").get_data(as_text=True))

    def test_signup_is_at_signup(self):
        body = _client().get("/signup").get_data(as_text=True)
        self.assertIn('name="password"', body)
        self.assertIn('href="/building"', body)

    def test_a_signup_form_open_from_before_the_move_still_submits(self):
        app = _build_test_app(sign_up_result=True)
        response = app.test_client().post("/", data={"email": "a@example.com", "password": "hunter22"})
        self.assertEqual(response.status_code, 307)
        self.assertTrue(response.headers["Location"].endswith("/signup"))

    def test_signup_is_still_rate_limited(self):
        source = inspect.getsource(routes)
        at = source.index('@main_bp.route("/signup", methods=["GET", "POST"])')
        self.assertIn('@limiter.limit("10 per minute", methods=["POST"])', source[at:at + 140])


class ConfirmationEmailTests(unittest.TestCase):
    def test_signup_asks_supabase_to_send_confirmations_to_login(self):
        app = _build_test_app(sign_up_result=True)
        app.test_client().post("/signup", data={"email": "a@example.com", "password": "hunter22"})
        service = app.config["SUPABASE_SERVICE"]
        self.assertEqual(service.redirect_targets, ["http://localhost/login"])

    def _service_with_recording_client(self):
        calls = []

        class Auth:
            def sign_up(self, credentials):
                calls.append(credentials)
                user = type("U", (), {"identities": [{"id": "x"}]})()
                return type("R", (), {"user": user})()

        client = type("C", (), {"auth": Auth()})()
        return ss.SupabaseService(client=client), calls

    def test_redirect_is_passed_to_supabase_as_email_redirect_to(self):
        service, calls = self._service_with_recording_client()
        service.sign_up("a@example.com", "hunter22", email_redirect_to="https://x.example/login")
        self.assertEqual(calls[0]["options"], {"email_redirect_to": "https://x.example/login"})

    def test_no_redirect_means_no_options(self):
        service, calls = self._service_with_recording_client()
        service.sign_up("a@example.com", "hunter22")
        self.assertNotIn("options", calls[0])


class ErrorPageTests(unittest.TestCase):
    def test_start_page_link_goes_to_the_new_front_door(self):
        with open(routes.__file__.replace("routes.py", "templates/error.html"), encoding="utf-8") as fh:
            self.assertIn("url_for('main.home')", fh.read())


if __name__ == "__main__":
    unittest.main()
