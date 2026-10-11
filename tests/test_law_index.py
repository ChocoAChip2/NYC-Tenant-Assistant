"""Tests for /law, the index of every section the legal library holds.

Rows are the parser's real output for the ALP fixtures (see
tests/test_law_page.py), plus two State rows shaped like the live ones.
Supabase is faked at the query-builder level; nothing here touches the
network.
"""

import html
import os
import re
import unittest

import law_service
from tests.test_law_page import HMC, RTC, FakeClient, _client

_TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")


def _state(citation, source_key, title):
    return {
        "section_key": f"{source_key}:{citation}", "source_key": source_key, "citation": citation,
        "title": title, "repealed": False, "status": "active", "jurisdiction": "NYS",
    }


STATE = [
    _state("235-b", "nys-rpl-7", "Warranty of habitability"),
    _state("711", "nys-rpapl-7", "Grounds where landlord-tenant relationship exists"),
]
LIBRARY = [*HMC.values(), *RTC.values(), *STATE]


class _IndexCase(unittest.TestCase):
    def setUp(self):
        law_service.clear_cache()
        self.addCleanup(law_service.clear_cache)

    def get(self, path="/law", rows=None, error=None):
        db = FakeClient(LIBRARY if rows is None else rows, error)
        response = _client(db).get(path)
        return response, html.unescape(response.get_data(as_text=True)), db


class LawIndexPageTests(_IndexCase):
    def test_public_page_lists_every_section_with_a_link(self):
        response, body, _ = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body.count("<li>"), len(LIBRARY))
        self.assertIn(f"{len(LIBRARY)} sections.", body)
        self.assertIn('<a href="/law/27-2029"><span class="cite">§ 27-2029</span>Minimum temperature to be maintained', body)
        self.assertIn('<a href="/law/235-b"><span class="cite">§ 235-b</span>Warranty of habitability</a>', body)
        for row in LIBRARY:
            self.assertIn(f'href="/law/{row["citation"]}"', body)

    def test_trailing_slash_serves_the_same_page(self):
        response, body, _ = self.get("/law/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Law library", body)

    def test_groups_are_laws_by_plain_name_city_first_and_closed(self):
        _, body, _ = self.get()
        names = re.findall(r'<span class="law-name">([^<]+)</span>', body)
        self.assertEqual(names, [
            "Housing Maintenance Code", "Right to Counsel", "Real Property Law Article 7", "RPAPL Article 7",
        ])
        self.assertLess(body.index(">City law<"), body.index(">State law<"))
        self.assertLess(body.index(">State law<"), body.index("Real Property Law Article 7"))
        self.assertIn('<span class="law-detail">Admin Code Title 27, Chapter 2</span>', body)
        self.assertIn(f'<span class="count">{len(HMC)} sections</span>', body)
        self.assertIn('<span class="count">1 section</span>', body)
        self.assertEqual(body.count("<details>"), 4)
        self.assertNotIn("<details open", body)

    def test_source_keys_never_reach_the_page(self):
        orphan = dict(HMC["27-2029"], section_key="nyc-new:1-101", source_key="nyc-new", citation="1-101")
        _, body, _ = self.get(rows=[*LIBRARY, orphan])
        for key in ("nyc-hmc", "nyc-rtc", "nys-rpl-7", "nys-rpapl-7", "nyc-new"):
            self.assertNotIn(key, body)
        self.assertIn('<span class="law-name">Other sections</span>', body)
        self.assertIn('href="/law/1-101"', body)

    def test_sections_are_in_number_order(self):
        _, body, _ = self.get()
        hmc = [c for c in re.findall(r'href="/law/([^"]+)"', body) if c.startswith("27-")]
        self.assertEqual(hmc, sorted(hmc, key=law_service._citation_order))
        self.assertLess(law_service._citation_order("27-2005"), law_service._citation_order("27-2017.1"))
        self.assertLess(law_service._citation_order("27-2017.1"), law_service._citation_order("27-2017.10"))
        self.assertLess(law_service._citation_order("235-a"), law_service._citation_order("235-b"))
        self.assertLess(law_service._citation_order("8-102"), law_service._citation_order("8-102a"))

    def test_repealed_and_dropped_sections_are_listed_with_labels(self):
        self.assertTrue(HMC["27-2018"]["repealed"])
        rows = [HMC["27-2018"], HMC["27-2005"], dict(HMC["27-2029"], status="missing_from_source")]
        _, body, _ = self.get(rows=rows)
        items = {m.group(1): m.group(0) for m in re.finditer(r'<li><a href="/law/([^"]+)".*?</li>', body)}
        self.assertEqual(set(items), {"27-2018", "27-2005", "27-2029"})
        self.assertIn('<span class="tag">Repealed</span>', items["27-2018"])
        self.assertIn('<span class="tag">No longer in the code</span>', items["27-2029"])
        self.assertNotIn('class="tag"', items["27-2005"])

    def test_a_number_held_twice_links_to_the_page_that_shows_both(self):
        twin = dict(RTC["26-1301"], section_key="nyc-rent-control:26-1301", source_key="nyc-rent-control")
        _, body, _ = self.get(rows=[RTC["26-1301"], twin])
        self.assertEqual(body.count('href="/law/26-1301"'), 2)

    def test_a_number_the_section_page_cannot_open_is_listed_without_a_link(self):
        # The live library holds RPL "233-b*2" (Campgrounds), which /law/<citation> rejects.
        odd = _state("233-b*2", "nys-rpl-7", "Campgrounds")
        _, body, _ = self.get(rows=[*STATE, odd])
        self.assertIn('<li><span class="cite">§ 233-b*2</span>Campgrounds</li>', body)
        self.assertNotIn('href="/law/233-b', body)
        self.assertIn('<span class="count">2 sections</span>', body)

    def test_titles_are_escaped(self):
        response = _client(FakeClient([dict(HMC["27-2029"], title="<script>alert(1)</script>")])).get("/law")
        raw = response.get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", raw)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", raw)

    def test_one_narrow_read_then_the_hourly_index(self):
        db = FakeClient(LIBRARY)
        client = _client(db)
        for _ in range(3):
            self.assertEqual(client.get("/law").status_code, 200)
        self.assertEqual([e[1] for e in db.log if e[0] == "select"], ["citation,title,source_key,status,repealed,jurisdiction"])
        self.assertEqual(sum(1 for e in db.log if e[0] == "execute"), 1)
        self.assertEqual(law_service.linkable_citations(db, ["27-2029", "27-2018"]), {"27-2029"})
        self.assertEqual(sum(1 for e in db.log if e[0] == "execute"), 1)

    def test_library_down_is_a_503_with_the_official_sites(self):
        response, body, _ = self.get(error=RuntimeError("relation does not exist"))
        self.assertEqual(response.status_code, 503)
        self.assertIn("The law library didn't respond", body)
        self.assertIn(law_service.ALP_CODE_LIBRARY_URL, body)
        self.assertIn(law_service.NYS_LAWS_URL, body)
        self.assertNotIn("<details>", body)

    def test_empty_library_is_a_plain_notice(self):
        response, body, _ = self.get(rows=[])
        self.assertEqual(response.status_code, 200)
        self.assertIn("The library has no sections right now.", body)
        self.assertIn(law_service.ALP_CODE_LIBRARY_URL, body)

    def test_page_carries_branding_and_the_footer_disclaimer(self):
        import branding

        _, body, _ = self.get()
        self.assertIn(branding.PRODUCT_NAME, body)
        self.assertIn(branding.SHORT_DISCLAIMER, body)


class GoToASectionTests(_IndexCase):
    def test_a_number_lands_on_its_section(self):
        for typed, target in (("27-2005", "27-2005"), ("§ 27-2005", "27-2005"), ("§27-2005.", "27-2005"),
                              ("  235-B ", "235-b"), ("711", "711")):
            with self.subTest(typed=typed):
                response = _client(FakeClient(LIBRARY)).get("/law", query_string={"section": typed})
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.headers["Location"], f"/law/{target}")

    def test_the_landing_page_is_the_section(self):
        client = _client(FakeClient(LIBRARY))
        response = client.get("/law", query_string={"section": "§ 27-2005"}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('<span class="cite">§ 27-2005</span>', response.get_data(as_text=True))

    def test_a_bad_value_gets_a_friendly_message(self):
        for typed in ("heat", "27--1", "<script>", "27-2005;drop", "§", "9" * 60):
            with self.subTest(typed=typed):
                response = _client(FakeClient(LIBRARY)).get("/law", query_string={"section": typed})
                body = response.get_data(as_text=True)
                self.assertEqual(response.status_code, 400)
                self.assertIn("That doesn't look like a section number", body)
                self.assertIn('aria-invalid="true"', body)
                self.assertNotIn('value="<script>"', body)
                self.assertIn("<details>", body)

    def test_a_blank_box_is_just_the_page(self):
        response = _client(FakeClient(LIBRARY)).get("/law", query_string={"section": "   "})
        self.assertEqual(response.status_code, 200)

    def test_the_form_works_without_javascript(self):
        _, body, _ = self.get()
        self.assertIn('<form class="goto" method="get" action="/law">', body)
        self.assertIn('name="section"', body)
        self.assertIn('<label id="goto-heading" for="section">Go to a section</label>', body)


class WaysInTests(_IndexCase):
    def test_section_page_links_to_the_index(self):
        body = _client(FakeClient(LIBRARY)).get("/law/27-2029").get_data(as_text=True)
        self.assertIn('<a href="/law">Browse the law library</a>', body)

    def test_not_found_message_links_to_the_index(self):
        response = _client(FakeClient(LIBRARY)).get("/law/27-9999")
        self.assertEqual(response.status_code, 404)
        self.assertIn('<a href="/law">Browse the law library</a>', response.get_data(as_text=True))

    def test_resources_template_has_its_line(self):
        with open(os.path.join(_TEMPLATES, "resources.html"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("<a href=\"{{ url_for('main.law_index') }}\">{{ _(\"Browse the law library\") }}</a>", source)

    def test_index_is_not_in_the_header_nav(self):
        _, body, _ = self.get()
        nav = body[body.index("<nav"):body.index("</nav>")]
        self.assertNotIn('href="/law"', nav)


if __name__ == "__main__":
    unittest.main()
