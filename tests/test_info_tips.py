"""The "i" buttons (templates/_info.html) that hold detail pages don't show up front.

Added 2026-10-02 when the owner asked for less wording everywhere: the
short version stays on the page, the rest goes behind an "i".
"""

import os
import re
import unittest

import flask

from tests.app_test_support import configure_test_app

_TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")


def _render(source, **context):
    app = flask.Flask(__name__, template_folder=_TEMPLATES)
    app.secret_key = "t"
    configure_test_app(app)
    with app.test_request_context("/"):
        return flask.render_template_string(source, **context)


class MacroTests(unittest.TestCase):
    def test_plain_text_tip_is_an_accessible_toggle(self):
        html = _render('{% from "_info.html" import info as info_tip %}<p>Short.{{ info_tip("More words.") }}</p>')
        self.assertIn('<button type="button" class="info-btn" aria-expanded="false" aria-label="More information"', html)
        self.assertIn('<span class="info-pop" role="note">More words.</span>', html)
        # Only phrasing elements, so it is valid inside a <p>.
        self.assertNotRegex(html, r"<(div|details|section|p )")

    def test_text_is_escaped_and_call_blocks_allow_links(self):
        escaped = _render('{% from "_info.html" import info as info_tip %}{{ info_tip(t) }}', t="<b>x</b>")
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", escaped)
        linked = _render('{% from "_info.html" import info as info_tip %}'
                         '{% call info_tip(label="Why") %}See <a href="/x">this</a>.{% endcall %}')
        self.assertIn('<a href="/x">this</a>', linked)
        self.assertIn('aria-label="Why"', linked)

    def test_assets_have_no_served_comments(self):
        html = _render('{% from "_info.html" import info_assets %}{{ info_assets() }}')
        self.assertIn(".info-tip.open > .info-pop", html)
        self.assertIn('addEventListener("keydown"', html)
        self.assertNotIn("<!--", html)
        self.assertNotRegex(html, r"/\*")


class EveryPageThatUsesATipLoadsItsAssets(unittest.TestCase):
    def test_pages(self):
        users = []
        for name in sorted(os.listdir(_TEMPLATES)):
            if not name.endswith(".html") or name.startswith("_"):
                continue
            with open(os.path.join(_TEMPLATES, name), encoding="utf-8") as handle:
                source = handle.read()
            uses = bool(re.search(r"info_tip\(", source)) or "requirement.template" in source
            if uses:
                users.append(name)
                with self.subTest(page=name):
                    self.assertEqual(source.count("{{ info_assets() }}"), 1)
                    self.assertIn('import info as info_tip, info_assets', source)
        self.assertGreaterEqual(len(users), 6)


if __name__ == "__main__":
    unittest.main()
