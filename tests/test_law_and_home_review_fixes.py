"""Regression tests for the review of the /law page and home-page branches (2026-09-30).

A code review and a real-browser sweep of the three stacked branches found:
a HEAD-request hole in the shared lookup limit; bare front-page loads using
up that limit; a building page that could hang for up to 120 s on Supabase;
two real library sections (§ 27-2056.6.1, § 8-102a) that the citation check
rejected, one of which a truncated chip would have linked to the WRONG
section; and a few smaller page issues. See log/2026-09-30-law-page-review-fixes.txt.
"""

import os

os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-for-review-fixes")

import threading
import time
import unittest
from unittest import mock

import building_service
import law_service
from app import create_app
from rate_limit import limiter
from tests.test_law_page import HMC, FakeClient, _client
from tests.test_building_lookup import _CacheIsolation, fake_network
from tools.corpus.alp import parse_chapter

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "alp")


class CitationShapeTests(unittest.TestCase):
    def test_real_multi_level_and_lettered_sections_are_valid(self):
        for citation in ("27-2056.6.1", "8-102a", "27-2029", "26-504.1", "8-107"):
            with self.subTest(citation=citation):
                self.assertTrue(law_service.is_valid_citation(citation))

    def test_every_citation_the_parser_produces_from_the_fixtures_is_valid(self):
        for name in sorted(os.listdir(FIXTURES)):
            if not name.endswith(".xml"):
                continue
            with open(os.path.join(FIXTURES, name), "rb") as handle:
                sections = parse_chapter(handle.read(), source_key="x", authority="NYC Admin Code").sections
            for section in sections:
                with self.subTest(citation=section.citation):
                    self.assertTrue(law_service.is_valid_citation(section.citation))

    def test_non_ascii_digits_and_junk_are_rejected(self):
        for citation in ("٢٧-٢٠٢٩", "27-2029/", "../x", "27-2029 ", "a" * 50, "27-2029.1.2.3"):
            with self.subTest(citation=citation):
                self.assertFalse(law_service.is_valid_citation(citation))

    def test_hmc_chip_keeps_every_decimal_level(self):
        # Before: "27-2056.6.1" was cut to 27-2056.6, a different section,
        # and the chip would have linked there.
        labels = building_service.extract_citations("SECTION 27-2056.6.1 ADM CODE CORRECT THE LEAD-BASED PAINT HAZARD")
        self.assertIn("NYC Admin Code § 27-2056.6.1", labels)
        self.assertNotIn("NYC Admin Code § 27-2056.6", labels)


class LawUrlTests(unittest.TestCase):
    def test_section_sign_and_trailing_period_redirect_to_the_canonical_url(self):
        client = _client(FakeClient(HMC.values()))
        for raw in ("§27-2029", "§ 27-2029", "27-2029."):
            with self.subTest(raw=raw):
                response = client.get(f"/law/{raw}")
                self.assertEqual(response.status_code, 301)
                self.assertTrue(response.headers["Location"].endswith("/law/27-2029"))

    def test_junk_is_still_a_404(self):
        self.assertEqual(_client(FakeClient(HMC.values())).get("/law/<script>").status_code, 404)

    def test_repealed_section_is_labelled_repealed_not_amended(self):
        repealed = [row for row in HMC.values() if row.get("repealed")]
        self.assertTrue(repealed, "fixture has a repealed section")
        body = _client(FakeClient(HMC.values())).get(f"/law/{repealed[0]['citation']}").get_data(as_text=True)
        self.assertIn("<dt>Repealed</dt>", body)
        self.assertNotIn("<dt>Last amended</dt>", body)


class ChipIndexTests(unittest.TestCase):
    def setUp(self):
        law_service.clear_cache()
        self.addCleanup(law_service.clear_cache)

    def _executes(self, db):
        return sum(1 for entry in db.log if entry[0] == "execute")

    def test_building_pages_reuse_the_hourly_index(self):
        db = FakeClient(HMC.values())
        self.assertEqual(law_service.linkable_citations(db, ["27-2029"]), {"27-2029"})
        law_service.linkable_citations(db, ["27-2005", "27-2029"])
        law_service.linkable_citations(db, ["27-2013"])
        self.assertEqual(self._executes(db), 1)

    def test_a_failed_load_is_not_retried_on_every_page(self):
        db = FakeClient(error=RuntimeError("down"))
        for _ in range(5):
            self.assertEqual(law_service.linkable_citations(db, ["27-2029"]), set())
        self.assertEqual(self._executes(db), 1)

    def test_a_stale_index_is_used_when_the_refresh_fails(self):
        good = FakeClient(HMC.values())
        law_service.linkable_citations(good, ["27-2029"])
        with mock.patch.object(law_service, "INDEX_TTL_SECONDS", 0):
            links = law_service.linkable_citations(FakeClient(error=RuntimeError("down")), ["27-2029"])
        self.assertEqual(links, {"27-2029"})

    def test_a_hanging_library_does_not_hang_the_page(self):
        release = threading.Event()
        self.addCleanup(release.set)

        class Hanging(FakeClient):
            def table(self, name):
                release.wait(10)
                return super().table(name)

        with mock.patch.object(law_service, "READ_TIMEOUT_SECONDS", 0.2):
            started = time.monotonic()
            self.assertEqual(law_service.linkable_citations(Hanging(HMC.values()), ["27-2029"]), set())
            with self.assertRaises(law_service.LawUnavailable):
                law_service.get_sections(Hanging(HMC.values()), "27-2029")
        self.assertLess(time.monotonic() - started, 2)


class LookupLimitTests(unittest.TestCase):
    def setUp(self):
        limiter.reset()
        self.addCleanup(limiter.reset)
        self.client = create_app().test_client()

    def test_head_requests_count_against_the_lookup_limit(self):
        with mock.patch.object(building_service, "lookup",
                               side_effect=building_service.LookupUnavailable("offline in tests")) as lookup:
            codes = [self.client.head("/?address=231+echo+place").status_code for _ in range(31)]
        self.assertEqual(codes[-1], 429)
        self.assertEqual(lookup.call_count, 30)

    def test_opening_the_front_page_without_an_address_costs_nothing(self):
        codes = {self.client.get("/").status_code for _ in range(40)}
        self.assertEqual(codes, {200})


class SmallPageFixTests(_CacheIsolation):
    def test_learn_more_sends_logged_out_visitors_home_not_to_login(self):
        body = create_app().test_client().get("/learn-more").get_data(as_text=True)
        self.assertIn('href="/"', body)
        self.assertNotIn("Back to chat", body)

    def test_building_result_has_a_page_heading(self):
        patcher, _ = fake_network()
        with patcher:
            body = _client(FakeClient(HMC.values())).get("/building?address=231+echo+pl+bx").get_data(as_text=True)
        self.assertIn('<h1 class="match-label" id="match-label">', body)


if __name__ == "__main__":
    unittest.main()
