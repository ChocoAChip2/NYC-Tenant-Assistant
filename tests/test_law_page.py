"""Tests for /law/<citation> and the building page's links to it.

The library rows are the parser's real output for the ALP fixtures
(tests/fixtures/alp/), shaped the way corpus_upsert_sections stores them,
so the page is tested on real statute text, real history lines and real
publisher notes. Supabase is faked at the query-builder level.
"""

import html
import os
import unittest

import flask

import building_service as bs
import law_service
from routes import main_bp
from tests.app_test_support import configure_test_app
from tests.test_building_lookup import _CacheIsolation, fake_network
from tools.corpus.alp import parse_chapter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TEMPLATES = os.path.join(_ROOT, "templates")
_FIXTURES = os.path.join(_ROOT, "tests", "fixtures", "alp")


def _real_rows(file_id, source_key, **overrides):
    with open(os.path.join(_FIXTURES, f"{file_id}.xml"), "rb") as handle:
        sections = parse_chapter(handle.read(), source_key=source_key, authority="NYC Admin Code").sections
    rows = {}
    for s in sections:
        p = s.as_payload()
        rows[s.citation] = {
            "section_key": p["section_key"], "source_key": p["source_key"], "authority": p["authority"],
            "citation": p["citation"], "title": p["title"], "heading_path": p["heading_path"],
            "full_text": p["full_text"], "history": p["history"], "notes": p["notes"],
            "repealed": p["repealed"], "last_amended": p["last_amended"],
            "last_checked_at": "2026-10-01T09:03:11.123456+00:00", "official_url": p["official_url"],
            "status": "active",
        }
        rows[s.citation].update(overrides)
    return rows


HMC = _real_rows("0-0-0-60027", "nyc-hmc")
RTC = _real_rows("0-0-0-47826", "nyc-rtc")


class FakeQuery:
    def __init__(self, table):
        self.table = table
        self.filters = []

    def select(self, columns):
        self.table.log.append(("select", columns))
        return self

    def eq(self, column, value):
        self.filters.append(("eq", column, value))
        return self

    def in_(self, column, values):
        self.filters.append(("in", column, list(values)))
        return self

    def limit(self, n):
        return self

    def execute(self):
        self.table.log.append(("execute", self.filters))
        if self.table.error:
            raise self.table.error
        rows = self.table.rows
        for kind, column, value in self.filters:
            if kind == "eq":
                rows = [r for r in rows if r.get(column) == value]
            else:
                rows = [r for r in rows if r.get(column) in value]
        return type("Response", (), {"data": rows})()


class FakeClient:
    def __init__(self, rows=(), error=None):
        self.rows = list(rows)
        self.error = error
        self.log = []

    def table(self, name):
        assert name == "legal_sources", name
        return FakeQuery(self)


class FakeSupabaseService:
    def __init__(self, client):
        self.client = client


class FakeAI:
    def is_ready(self):
        return True


def _client(db):
    app = flask.Flask(__name__, template_folder=_TEMPLATES)
    app.secret_key = "t"
    app.config["SUPABASE_SERVICE"] = FakeSupabaseService(db)
    app.config["AI_SERVICE"] = FakeAI()
    app.register_blueprint(main_bp)
    configure_test_app(app)
    return app.test_client()


def _law_text(body):
    return body[body.index('<div class="law-text">'):body.index("</div>", body.index('<div class="law-text">'))]


class LawPageTests(unittest.TestCase):
    def get(self, citation, rows=None, error=None):
        db = FakeClient(rows if rows is not None else HMC.values(), error)
        response = _client(db).get(f"/law/{citation}")
        return response, response.get_data(as_text=True), db

    def test_heat_section_verbatim_with_provenance(self):
        response, body, _ = self.get("27-2029")
        self.assertEqual(response.status_code, 200)
        self.assertIn("a temperature of at least sixty-two degrees Fahrenheit.", _law_text(body))
        self.assertIn("§ 27-2029</span> Minimum temperature to be maintained", body)
        self.assertIn("Article 8: Heat, Cooling, and Hot Water", body)
        self.assertIn("(Am. L.L. 2017/086, 5/30/2017, eff. 10/1/2017)", body)
        self.assertIn("Effective October 1, 2017", body)
        self.assertIn("Matched the official code on October 1, 2026", body)
        self.assertIn('href="https://codelibrary.amlegal.com/codes/newyorkcity/latest/NYCadmin/0-0-0-60410"', body)
        self.assertNotIn("[ALP", body)

    def test_page_carries_branding_and_both_disclaimers(self):
        _, body, _ = self.get("27-2029")
        import branding

        self.assertIn(branding.PRODUCT_NAME, body)
        self.assertIn(branding.SHORT_DISCLAIMER, body)
        self.assertIn(branding.LAW_PAGE_NOTE, body)
        self.assertIn("not legal advice", branding.LAW_PAGE_NOTE)

    def test_publisher_notes_are_labelled_and_kept_out_of_the_law_text(self):
        _, body, _ = self.get("27-2004")
        body = html.unescape(body)
        self.assertIn("Publisher's notes", body)
        self.assertIn("Not part of the law.", body)
        self.assertIn("Editor's note: For related unconsolidated provisions", body)
        self.assertNotIn("Editor's note", _law_text(body))

    def test_repealed_section_says_so(self):
        response, body, _ = self.get("27-2018")
        self.assertEqual(response.status_code, 200)
        self.assertIn("This section has been repealed", body)
        self.assertIn("(Repealed L.L. 2018/055, 1/19/2018, eff. 1/19/2019)", body)
        self.assertNotIn('<div class="law-text">', body)

    def test_section_that_left_the_code_is_shown_with_a_warning(self):
        rows = [dict(HMC["27-2029"], status="missing_from_source")]
        response, body, _ = self.get("27-2029", rows)
        self.assertEqual(response.status_code, 200)
        self.assertIn("No longer in the official code", body)
        self.assertIn("Don't rely on it as current law.", body)
        self.assertIn("sixty-two degrees", body)

    def test_two_sections_with_one_number_are_both_shown(self):
        other = dict(RTC["26-1301"], section_key="nyc-other:26-1301", source_key="nyc-other",
                     heading_path="Chapter 13: Certification of Certain Rent Payment")
        response, body, _ = self.get("26-1301", [RTC["26-1301"], other])
        self.assertEqual(response.status_code, 200)
        self.assertIn("More than one section is numbered § 26-1301.", body)
        self.assertIn("Provision of Legal Services in Eviction Proceedings", body)
        self.assertIn("Certification of Certain Rent Payment", body)

    def test_unknown_section_is_a_404_with_a_way_forward(self):
        response, body, _ = self.get("27-9999")
        self.assertEqual(response.status_code, 404)
        self.assertIn("isn't in our library", body)
        self.assertIn(law_service.ALP_CODE_LIBRARY_URL, body)

    def test_malformed_citation_is_a_404_without_a_query(self):
        for bad in ("abc", "27-2029;drop", "27--1", "1234-1"):
            with self.subTest(bad=bad):
                response, _, db = self.get(bad)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(db.log, [])

    def test_library_down_is_a_503_not_a_crash(self):
        response, body, _ = self.get("27-2029", error=RuntimeError("relation does not exist"))
        self.assertEqual(response.status_code, 503)
        self.assertIn("The law library didn't respond", body)

    def test_no_supabase_client_is_a_503(self):
        app = flask.Flask(__name__, template_folder=_TEMPLATES)
        app.secret_key = "t"
        app.config["SUPABASE_SERVICE"] = object()
        app.config["AI_SERVICE"] = FakeAI()
        app.register_blueprint(main_bp)
        configure_test_app(app)
        self.assertEqual(app.test_client().get("/law/27-2029").status_code, 503)

    def test_row_without_an_https_source_is_not_shown(self):
        rows = [dict(HMC["27-2029"], official_url="http://example.com/x")]
        response, _, _ = self.get("27-2029", rows)
        self.assertEqual(response.status_code, 404)

    def test_text_is_escaped(self):
        rows = [dict(HMC["27-2029"], full_text="<script>alert(1)</script>", title="<b>x</b>")]
        _, body, _ = self.get("27-2029", rows)
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", body)
        self.assertNotIn("<b>x</b>", body)


class LinkableCitationTests(unittest.TestCase):
    def setUp(self):
        law_service.clear_cache()
        self.addCleanup(law_service.clear_cache)

    def test_links_only_unambiguous_active_unrepealed_sections(self):
        rows = list(HMC.values()) + [RTC["26-1301"]]
        db = FakeClient(rows)
        linkable = law_service.linkable_citations(db, ["27-2029", "27-2018", "26-1301", "27-9999"])
        self.assertEqual(linkable, {"27-2029"})  # 27-2018 repealed, 26-1301 ambiguous, 27-9999 absent

    def test_one_query_for_the_whole_page(self):
        db = FakeClient(HMC.values())
        law_service.linkable_citations(db, ["27-2029", "27-2005", "27-2013", "27-2029"])
        self.assertEqual(sum(1 for entry in db.log if entry[0] == "execute"), 1)

    def test_a_number_held_twice_is_not_linked(self):
        twin = dict(HMC["27-2029"], section_key="nyc-x:27-2029", source_key="nyc-x")
        self.assertEqual(law_service.linkable_citations(FakeClient([HMC["27-2029"], twin]), ["27-2029"]), set())

    def test_failure_means_no_links(self):
        self.assertEqual(law_service.linkable_citations(FakeClient(error=RuntimeError("down")), ["27-2029"]), set())
        self.assertEqual(law_service.linkable_citations(None, ["27-2029"]), set())

    def test_chip_label_parsing(self):
        self.assertEqual(law_service.admin_code_number("NYC Admin Code § 27-2029"), "27-2029")
        self.assertEqual(law_service.admin_code_number("NYC Admin Code § 27-2017.4"), "27-2017.4")
        self.assertIsNone(law_service.admin_code_number("28 RCNY § 25-01"))
        self.assertIsNone(law_service.admin_code_number("Multiple Dwelling Law § 309"))


class BuildingChipLinkTests(_CacheIsolation):
    def _building(self, db):
        patcher, _ = fake_network()
        with patcher:
            return _client(db).get("/building?address=231+echo+pl+bx").get_data(as_text=True)

    def test_chips_link_to_sections_the_library_holds(self):
        body = self._building(FakeClient(HMC.values()))
        self.assertIn('<a class="chip" href="/law/27-2029">NYC Admin Code § 27-2029</a>', body)
        self.assertIn('<a class="chip" href="/law/27-2005">NYC Admin Code § 27-2005</a>', body)

    def test_sections_not_in_the_library_stay_plain(self):
        rows = [HMC["27-2029"]]
        body = self._building(FakeClient(rows))
        self.assertIn('<span class="chip">NYC Admin Code § 27-2005</span>', body)

    def test_library_failure_leaves_the_building_page_intact(self):
        body = self._building(FakeClient(error=RuntimeError("relation legal_sources has no column status")))
        self.assertIn("231 ECHO PLACE, Bronx, NY, USA", body)
        self.assertIn('<span class="chip">NYC Admin Code § 27-2029</span>', body)
        self.assertNotIn('<a class="chip"', body)


if __name__ == "__main__":
    unittest.main()
