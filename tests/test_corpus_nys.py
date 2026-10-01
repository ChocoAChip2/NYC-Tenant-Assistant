"""Tests for state law from the NY Senate Open Legislation API (tools/corpus/nys.py).

The fixture is an excerpt of a real response captured 2026-10-01
(GET /api/3/laws/RPP?full=true): Article 6-A whole (Good Cause, 9
sections) and six sections of Article 7. Nothing here touches the network.
"""

import io
import json
import os
import unittest
import urllib.error
from datetime import date

from tools.corpus import nys
from tools.corpus.model import law_hash
from tools.corpus.registry import NYS_SOURCES
from tests.test_corpus_refresh import SUPABASE_URL, FakeNetwork, RefreshTestCase, _Response

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "nys", "rpp_tree_excerpt.json")
KEY = "test-key-0123456789"


def _tree():
    with open(FIXTURE, encoding="utf-8") as handle:
        return json.load(handle)


def _parse(scope, key="nys-x"):
    return nys.parse_law(_tree(), law_id="RPP", law_name="Real Property Law", source_key=key,
                         authority="NY Real Property Law", scope=scope)


class ParseTests(unittest.TestCase):
    def test_good_cause_whole_article(self):
        result = _parse(("A6-A",))
        self.assertEqual([s.citation for s in result.sections],
                         ["210", "211", "212", "213", "214", "215", "216", "217", "218"])
        self.assertEqual(result.warnings, [])

    def test_text_is_unwrapped_into_paragraphs(self):
        (habitability,) = [s for s in _parse(("A7",)).sections if s.citation == "235-b"]
        self.assertTrue(habitability.paragraphs[0].startswith("§ 235-b. Warranty of habitability. 1. In every written"))
        self.assertTrue(habitability.paragraphs[1].startswith("2. Any agreement by a lessee"))
        self.assertNotIn("\\n", habitability.text)
        self.assertIn("fit for human habitation and for the uses reasonably intended", habitability.text)

    def test_publisher_notes_are_notes_not_law(self):
        (applicability,) = [s for s in _parse(("A6-A",)).sections if s.citation == "212"]
        self.assertEqual(applicability.notes, ["NB Repealed June 15, 2034"])
        self.assertEqual(applicability.paragraphs, [
            "§ 212. Applicability in the city of New York. Upon the effective date of this section, "
            "this article shall apply to the city of New York."])

    def test_identity_urls_and_dates(self):
        sections = {s.citation: s for s in _parse(("A7",), key="nys-rpl-7").sections}
        habitability = sections["235-b"]
        self.assertEqual(habitability.section_key, "nys-rpl-7:235-b")
        self.assertEqual(habitability.official_url, "https://www.nysenate.gov/legislation/laws/RPP/235-B")
        self.assertEqual(habitability.last_amended, "2014-09-22")
        self.assertEqual(habitability.heading_path, "Article 7: Landlord and Tenant")
        self.assertEqual(sections["233-b*2"].official_url, "https://www.nysenate.gov/legislation/laws/RPP/233-B%2A2")

    def test_payload_is_state_jurisdiction_and_hash_matches_the_server_rule(self):
        payload = _parse(("A7",)).sections[0].as_payload()
        self.assertEqual(payload["jurisdiction"], "NYS")
        self.assertEqual(payload["content_hash"], law_hash(payload["full_text"]))
        self.assertRegex(payload["section_key"], r"^[a-z0-9-]{2,40}:")

    def test_missing_scope_is_a_warning_not_a_crash(self):
        result = _parse(("A99",))
        self.assertEqual(result.sections, [])
        self.assertIn("A99", result.warnings[0])

    def test_title_scope(self):
        tree = {"result": {"documents": {"documents": {"items": [
            {"docType": "ARTICLE", "locationId": "A7", "docLevelId": "7", "title": "Security", "documents": {"items": [
                {"docType": "TITLE", "locationId": "A7T1", "docLevelId": "1", "title": "Deposits", "documents": {"items": [
                    {"docType": "SECTION", "locationId": "7-108", "title": "Deposits", "activeDate": "2019-07-14",
                     "repealed": False, "text": "  § 7-108. Deposits. 1. This section\\napplies.",
                     "documents": {"items": []}}]}},
                {"docType": "TITLE", "locationId": "A7T2", "title": "Taxicabs", "documents": {"items": [
                    {"docType": "SECTION", "locationId": "7-201", "title": "Taxi", "repealed": False,
                     "text": "  § 7-201. Taxi.", "documents": {"items": []}}]}}]}}]}}}}
        result = nys.parse_law(tree, law_id="GOB", law_name="General Obligations Law", source_key="nys-gol",
                               authority="NY General Obligations Law", scope=("A7", "A7T1"))
        self.assertEqual([s.citation for s in result.sections], ["7-108"])
        self.assertEqual(result.sections[0].text, "§ 7-108. Deposits. 1. This section applies.")
        # Readable levels, not the API's location ids ("A7 > A7T1"), found live 2026-10-01.
        self.assertEqual(result.sections[0].heading_path, "Article 7: Security > Title 1: Deposits")

    def test_split_text_keeps_hyphenated_words_whole(self):
        paragraphs, _ = nys.split_text("  1. A rent-\\nstabilized unit and a\\nlease.")
        self.assertEqual(paragraphs, ["1. A rent-stabilized unit and a lease."])


class DatedVersionTests(unittest.TestCase):
    """The API prints every dated version of a provision; only today's is law.

    Shape copied from RPAPL § 711 as served on 2026-10-01: subdivision 2 in
    its Good Cause form ("Effective until June 15, 2034"), then its
    post-sunset form ("Effective June 15, 2034").
    """

    TEXT = ("  § 711. Grounds. A tenant shall include an occupant.\\n"
            "  1. The tenant continues in possession.\\n"
            "  * 2. Current: served upon the tenant as\\nprescribed.\\n"
            "  * NB Effective until June 15, 2034\\n"
            "  * 2. Future: served upon him as prescribed.\\n"
            "  * NB Effective June 15, 2034\\n"
            "  3. The tenant defaults in taxes.")

    def test_today_keeps_the_version_in_force_and_says_one_was_left_out(self):
        paragraphs, notes = nys.split_text(self.TEXT, date(2026, 10, 1))
        self.assertEqual([p[:10] for p in paragraphs], ["§ 711. Gro", "1. The ten", "2. Current", "3. The ten"])
        self.assertIn("2. Current: served upon the tenant as prescribed.", paragraphs)
        self.assertIn("1 paragraph(s) of a version not in force on 2026-10-01 are not shown", notes)

    def test_after_the_sunset_the_other_version_is_the_law(self):
        paragraphs, _ = nys.split_text(self.TEXT, date(2034, 6, 15))
        self.assertIn("2. Future: served upon him as prescribed.", paragraphs)
        self.assertFalse(any(p.startswith("2. Current") for p in paragraphs))

    def test_a_sunset_section_is_law_until_its_date(self):
        text = "  * § 212. Applicability. This article applies.\\n  * NB Repealed June 15, 2034"
        self.assertEqual(nys.split_text(text, date(2026, 10, 1))[0], ["§ 212. Applicability. This article applies."])
        self.assertEqual(nys.split_text(text, date(2034, 6, 15))[0], [])

    def test_in_force_reads_the_note_forms_the_api_uses(self):
        today = date(2026, 10, 1)
        self.assertTrue(nys.in_force("NB Effective August 18, 2024 until June 15, 2034", today))
        self.assertFalse(nys.in_force("NB Effective June 15, 2034", today))
        self.assertTrue(nys.in_force("NB Effective June 15, 2020", today))
        self.assertTrue(nys.in_force("NB Repealed June 15, 2034", today))
        self.assertFalse(nys.in_force("NB Repealed June 30, 2025", today))
        self.assertTrue(nys.in_force("NB There are 2 sub 5's", today))
        self.assertTrue(nys.in_force("NB Effective Smarch 40, 2020", today))  # unreadable: keep


class RegistryTests(unittest.TestCase):
    def test_every_state_source_has_a_count_anchors_and_a_valid_law_id(self):
        for source in NYS_SOURCES:
            with self.subTest(source=source.key):
                self.assertTrue(source.real_count and source.min_sections)
                self.assertTrue(source.anchors)
                self.assertRegex(source.law_id, r"^[A-Z]{3}$")
                self.assertEqual(source.jurisdiction, "NYS")

    def test_fixture_anchors_for_the_articles_it_holds_whole(self):
        citations = {s.citation for s in _parse(("A6-A",)).sections}
        (good_cause,) = [s for s in NYS_SOURCES if s.key == "nys-good-cause"]
        self.assertTrue(set(good_cause.anchors) <= citations)
        self.assertEqual(len(citations), good_cause.real_count)


class NysNetwork(FakeNetwork):
    """FakeNetwork plus the NY Senate API, serving the fixture for RPP."""

    def __init__(self, *args, api_error=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.api_error = api_error
        self.api_urls = []

    def __call__(self, request, timeout=None):
        if request.full_url.startswith(nys.API_BASE):
            self.api_urls.append(request.full_url)
            if self.api_error:
                raise self.api_error
            if "/RPP?" not in request.full_url:
                raise AssertionError(f"unexpected law {request.full_url}")
            with open(FIXTURE, "rb") as handle:
                return _Response(handle.read())
        return super().__call__(request, timeout)


class RefreshWithStateLawTests(RefreshTestCase):
    ENV = {"NYSENATE_API_KEY": KEY, "SUPABASE_URL": SUPABASE_URL, "SUPABASE_KEY": "anon",
           "CORPUS_INGEST_TOKEN": "tok"}

    def test_dry_run_of_a_state_source(self):
        net = NysNetwork()
        code, out, _ = self.run_refresh(["--dry-run", "--source", "nys-good-cause"], net, env=self.ENV)
        self.assertEqual(code, 0, out)
        self.assertIn("| nys-good-cause | 9 | 9 | 0 | 0 | 0 | 0 |", out)
        self.assertIn("NY Senate Open Legislation", out)
        self.assertEqual(len(net.api_urls), 1)
        self.assertIn("full=true", net.api_urls[0])

    def test_one_request_per_law_and_gates_still_apply(self):
        net = NysNetwork()
        code, out, _ = self.run_refresh(["--dry-run", "--source", "nys-good-cause", "--source", "nys-rpl-7"],
                                        net, env=self.ENV)
        self.assertEqual(len(net.api_urls), 1)  # both are RPP
        self.assertEqual(code, 3)  # the excerpt has 6 of Article 7's 55 sections
        self.assertIn("nys-rpl-7: parsed 6 sections, minimum is 49", out)

    def test_write_marks_rows_as_state_law(self):
        net = NysNetwork()
        code, out, net = self.run_refresh(["--source", "nys-good-cause"], net, env=self.ENV)
        self.assertEqual(code, 0, out)
        upserts = [body for method, url, body, _ in net.calls if url.endswith("/rpc/corpus_upsert_sections")]
        self.assertTrue(all(s["jurisdiction"] == "NYS" for s in upserts[0]["p_sections"]))
        self.assertEqual(len(net.rows), 9)

    def test_without_a_key_the_state_sources_are_skipped_and_named(self):
        with open(self.zip_path, "rb") as handle:
            net = NysNetwork(zip_bytes=handle.read())
        env = {k: v for k, v in self.ENV.items() if k != "NYSENATE_API_KEY"}
        code, out, _ = self.run_refresh(["--dry-run", "--from-zip", self.zip_path], net, env=env)
        self.assertEqual(net.api_urls, [])
        self.assertIn("Skipped (NYSENATE_API_KEY not set): nys-good-cause", out)

    def test_asking_for_a_state_source_without_a_key_is_an_error(self):
        env = {k: v for k, v in self.ENV.items() if k != "NYSENATE_API_KEY"}
        code, out, _ = self.run_refresh(["--dry-run", "--source", "nys-rpl-7"], NysNetwork(), env=env)
        self.assertEqual(code, 2)
        self.assertIn("NYSENATE_API_KEY", out)

    def test_the_key_never_appears_in_the_summary_even_on_failure(self):
        error = urllib.error.URLError(f"timed out fetching ...?key={KEY}")
        code, out, _ = self.run_refresh(["--dry-run", "--source", "nys-good-cause"],
                                        NysNetwork(api_error=error), env=self.ENV)
        self.assertEqual(code, 2)
        self.assertNotIn(KEY, out)
        self.assertIn("<key>", out)

    def test_an_api_http_error_is_a_clean_exit(self):
        error = urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b"{}"))
        code, out, _ = self.run_refresh(["--dry-run", "--source", "nys-good-cause"],
                                        NysNetwork(api_error=error), env=self.ENV)
        self.assertEqual(code, 2)
        self.assertIn("HTTP 403", out)
        self.assertNotIn(KEY, out)


if __name__ == "__main__":
    unittest.main()
