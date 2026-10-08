"""Replies must never show a bare "[S5]" (found testing the live site, 2026-09-30).

The markers mean "passage 5 of this request" and are resolved into the
section they name before a reply is stored (citation_guard.resolve_markers).
Replies stored before that fix still carry them, so the chat page strips
them on display.
"""

import os
import unittest

import flask

from routes import main_bp
from tests.app_test_support import configure_test_app
from tests.test_conversation_create_rename_and_cleanup import FakeSupabaseService, _logged_in_session

TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")


class LegacyMarkers(FakeSupabaseService):
    def fetch_messages_for_conversation(self, user_client, conversation_id):
        return [{"role": "assistant", "created_at": "",
                 "content": 'It must be at least "sixty-two degrees Fahrenheit" between 10 p.m. and 6 a.m. [S5].'}]

    def get_case_context(self, client, conversation_id):
        import case_context
        return {"context": case_context.defaults(), "revision": 0}


class FakeAI:
    def is_ready(self):
        return True


class StoredMarkerTests(unittest.TestCase):
    def test_old_replies_are_shown_without_bare_markers(self):
        app = flask.Flask(__name__, template_folder=TEMPLATES)
        app.secret_key = "test"
        app.config["SUPABASE_SERVICE"] = LegacyMarkers()
        app.config["AI_SERVICE"] = FakeAI()
        app.register_blueprint(main_bp)
        configure_test_app(app)
        client = app.test_client()
        _logged_in_session(client)
        body = client.get("/chat?conversation_id=c1").get_data(as_text=True)
        self.assertIn("6 a.m.", body)
        self.assertNotIn("[S5]", body)


if __name__ == "__main__":
    unittest.main()
