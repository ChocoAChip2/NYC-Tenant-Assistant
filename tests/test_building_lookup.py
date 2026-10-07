"""Tests for the public building lookup.

Everything network-facing is faked by URL, so the suite never depends on
NYC's APIs being up. The fixtures below are shaped exactly like real
responses captured while building this (a GeoSearch hit for 231 Echo
Place, Bronx, and HPD violation rows from dataset wvxf-dwi5).

The tests worth reading first are the HONESTY ones: the data date must
come from the city, never from our clock; a wrong geocode must be visible;
"certified" must be distinguishable from "fixed"; and a query must be
impossible to steer with a crafted BBL.
"""

import html
import os
import unittest
import urllib.error
from datetime import date, datetime, timezone
from unittest import mock

import flask

import building_service as bs
from routes import main_bp
from tests.app_test_support import configure_test_app

_TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")

BBL = "2028100045"

GEOSEARCH_HIT = {
    "features": [
        {
            "properties": {
                "label": "231 ECHO PLACE, Bronx, NY, USA",
                "name": "231 ECHO PLACE",
                "borough": "Bronx",
                "confidence": 0.8,
                "addendum": {"pad": {"bbl": BBL, "bin": "2007730"}},
            }
        }
    ]
}

# A park / neighbourhood result has no PAD addendum: nothing to look up.
GEOSEARCH_NON_BUILDING_THEN_BUILDING = {
    "features": [
        {"properties": {"label": "Echo Park", "borough": "Bronx"}},
        GEOSEARCH_HIT["features"][0],
    ]
}

ROW_C_HEAT = {
    "violationid": "1", "class": "C",
    "novdescription": "§ 27-2029 ADM CODE PROVIDE HEAT TO THE ENTIRE APARTMENT LOCATED AT APT 4B, 4th STORY",
    "currentstatus": "NOV SENT OUT", "currentstatusdate": "2026-02-10T00:00:00.000",
    "inspectiondate": "2026-02-03T00:00:00.000", "apartment": "4B", "story": "4", "rentimpairing": "Y",
}
ROW_C_OLDER = {
    "violationid": "2", "class": "C",
    "novdescription": "§ 27-2005 ADM CODE & 309 M/D LAW ABATE THE NUISANCE CONSISTING OF MICE",
    "currentstatus": "NOV CERTIFIED LATE", "currentstatusdate": "2025-11-01T00:00:00.000",
    "inspectiondate": "2025-10-01T00:00:00.000", "apartment": "2A", "story": "2", "rentimpairing": "N",
}
ROW_B = {
    "violationid": "3", "class": "B",
    "novdescription": "§ 27-2005 ADM CODE PROPERLY REPAIR THE BROKEN OR DEFECTIVE VENTILATION SYSTEM",
    "currentstatus": "NOT COMPLIED WITH", "inspectiondate": "2026-03-01T00:00:00.000",
    "apartment": "4B", "story": "4", "rentimpairing": "N",
}
ROW_A = {
    "violationid": "4", "class": "A",
    "novdescription": "§ 27-2013 ADM CODE PAINT WITH LIGHT COLORED PAINT",
    "currentstatus": "NOV SENT OUT", "inspectiondate": "2026-04-01T00:00:00.000",
    "apartment": "", "story": "", "rentimpairing": "N",
}

AS_OF_EPOCH = int(datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc).timestamp())


def fake_network(
    geocode=GEOSEARCH_HIT,
    open_rows=(ROW_A, ROW_B, ROW_C_OLDER, ROW_C_HEAT),
    open_counts=None,
    history_counts=None,
    metadata=None,
    fail_on=None,
):
    """Stand-in for building_service._get_json, dispatching on URL + query."""
    calls = []
    open_counts = open_counts or [{"class": "C", "n": "2"}, {"class": "B", "n": "1"}, {"class": "A", "n": "1"}]
    history_counts = history_counts or [{"class": "C", "n": "9"}, {"class": "B", "n": "20"}, {"class": "A", "n": "14"}]
    metadata = metadata if metadata is not None else {"rowsUpdatedAt": AS_OF_EPOCH}

    def _get_json(url, params=None):
        calls.append((url, dict(params or {})))
        if fail_on and fail_on(url, params or {}):
            raise bs.LookupUnavailable("simulated outage")
        if url == bs.GEOSEARCH_URL:
            return geocode
        if url == bs.VIOLATIONS_METADATA_URL:
            return metadata
        if url == bs.VIOLATIONS_URL:
            where = (params or {}).get("$where", "")
            if "$group" in (params or {}):
                return open_counts if "violationstatus='Open'" in where else history_counts
            return list(open_rows)
        raise AssertionError(f"unexpected URL {url}")

    patcher = mock.patch.object(bs, "_get_json", side_effect=_get_json)
    return patcher, calls


class _CacheIsolation(unittest.TestCase):
    def setUp(self):
        import law_service

        bs.clear_caches()
        law_service.clear_cache()
        self.addCleanup(bs.clear_caches)
        self.addCleanup(law_service.clear_cache)


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------


class AddressInputTests(_CacheIsolation):
    def test_blank_is_rejected(self):
        for value in ("", "   ", None):
            with self.subTest(value=value), self.assertRaises(bs.InvalidAddress):
                bs.clean_address(value)

    def test_an_address_without_a_house_number_is_rejected(self):
        """GeoSearch resolves a bare street name to SOME building on it,
        which would show a tenant the wrong building's violations."""
        with self.assertRaises(bs.InvalidAddress):
            bs.clean_address("Echo Place Bronx")

    def test_an_absurdly_long_address_is_rejected(self):
        with self.assertRaises(bs.InvalidAddress):
            bs.clean_address("1 " + "x" * bs.MAX_ADDRESS_LENGTH)

    def test_whitespace_is_collapsed(self):
        self.assertEqual(bs.clean_address("  231   Echo  Pl \n Bx "), "231 Echo Pl Bx")

    def test_apartment_spellings_normalise_to_one_form(self):
        for raw in ("4B", "4b", "Apt 4B", "Apt. 4b", "apartment 4B", "#4B", "unit 4-B", " 4 B "):
            with self.subTest(raw=raw):
                self.assertEqual(bs.normalize_apartment(raw), "4B")

    def test_blank_apartment_is_none(self):
        for raw in ("", None, "   ", "apt", "#"):
            with self.subTest(raw=raw):
                self.assertIsNone(bs.normalize_apartment(raw))


class QueryInjectionTests(_CacheIsolation):
    """The BBL is interpolated into a SoQL WHERE clause. is_valid_bbl is
    the only thing standing between that and a crafted query."""

    def test_only_ten_digit_bbls_in_a_real_borough_are_valid(self):
        self.assertTrue(bs.is_valid_bbl("2028100045"))
        for bad in (
            "", None, "202810004", "20281000451", "0028100045", "6028100045",
            "2028100045' OR '1'='1", "2028100045'--", "20281000a5", " 2028100045",
        ):
            with self.subTest(bad=bad):
                self.assertFalse(bs.is_valid_bbl(bad))

    def test_the_query_functions_refuse_a_malformed_bbl_before_any_request(self):
        with mock.patch.object(bs, "_get_json") as get_json:
            with self.assertRaises(ValueError):
                bs._fetch_open("1' OR '1'='1")
            with self.assertRaises(ValueError):
                bs._fetch_counts("1' OR '1'='1", "violationstatus='Open'")
        get_json.assert_not_called()

    def test_a_geocode_result_with_a_malformed_bbl_is_skipped_not_queried(self):
        hostile = {"features": [{"properties": {"label": "x", "addendum": {"pad": {"bbl": "1' OR '1'='1"}}}}]}
        patcher, calls = fake_network(geocode=hostile)
        with patcher, self.assertRaises(bs.AddressNotFound):
            bs.lookup("231 Echo Place")
        self.assertFalse(any(url == bs.VIOLATIONS_URL for url, _ in calls))


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------


class GeocodeTests(_CacheIsolation):
    def test_returns_the_label_bbl_and_bin(self):
        patcher, _ = fake_network()
        with patcher:
            match = bs.geocode("231 echo pl bx")

        self.assertEqual(match.label, "231 ECHO PLACE, Bronx, NY, USA")
        self.assertEqual(match.bbl, BBL)
        self.assertEqual(match.bin, "2007730")

    def test_skips_results_that_are_not_buildings(self):
        patcher, _ = fake_network(geocode=GEOSEARCH_NON_BUILDING_THEN_BUILDING)
        with patcher:
            self.assertEqual(bs.geocode("231 echo").bbl, BBL)

    def test_no_building_at_all_is_address_not_found(self):
        patcher, _ = fake_network(geocode={"features": []})
        with patcher, self.assertRaises(bs.AddressNotFound):
            bs.geocode("99999 Nowhere Street")

    def test_a_malformed_response_is_unavailable_not_a_crash(self):
        for payload in (None, [], {"features": "nope"}, "garbage"):
            with self.subTest(payload=payload):
                bs.clear_caches()
                patcher, _ = fake_network(geocode=payload)
                with patcher, self.assertRaises(bs.LookupUnavailable):
                    bs.geocode("231 Echo Place")

    def test_repeat_lookups_are_served_from_cache(self):
        patcher, calls = fake_network()
        with patcher:
            bs.geocode("231 Echo Place")
            bs.geocode("  231   echo place ")

        self.assertEqual(sum(1 for url, _ in calls if url == bs.GEOSEARCH_URL), 1)


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


class ReportTests(_CacheIsolation):
    def _report(self, **kwargs):
        apartment = kwargs.pop("apartment", None)
        patcher, _ = fake_network(**kwargs)
        with patcher:
            return bs.lookup("231 Echo Place, Bronx", apartment)

    def test_most_serious_first_then_newest_first(self):
        report = self._report()

        order = [(v.violation_class, v.violation_id) for v in report.open_violations]
        self.assertEqual(order, [("C", "1"), ("C", "2"), ("B", "3"), ("A", "4")])

    def test_counts_come_from_the_aggregate_not_the_capped_list(self):
        """The list is capped; the numbers at the top must not be."""
        report = self._report(open_counts=[{"class": "C", "n": "412"}, {"class": "B", "n": "3"}])

        self.assertEqual(report.open_counts["C"], 412)
        self.assertEqual(report.open_total, 415)

    def test_truncation_is_flagged_when_the_list_hits_the_cap(self):
        rows = [dict(ROW_A, violationid=str(i)) for i in range(bs.MAX_OPEN_VIOLATIONS)]
        self.assertTrue(self._report(open_rows=rows).list_truncated)
        bs.clear_caches()
        self.assertFalse(self._report().list_truncated)

    def test_certified_is_counted_separately_from_open(self):
        """A certification is the LANDLORD saying it's fixed."""
        report = self._report()

        self.assertEqual(report.certified_open, 1)
        certified = [v for v in report.open_violations if v.certified]
        self.assertEqual([v.violation_id for v in certified], ["2"])

    def test_rent_impairing_is_counted(self):
        self.assertEqual(self._report().rent_impairing_open, 1)

    def test_history_counts_cover_the_window(self):
        self.assertEqual(self._report().history_total, 43)

    def test_apartment_filter_matches_any_spelling(self):
        report = self._report(apartment="apt. 4b")

        self.assertEqual(report.apartment, "4B")
        self.assertEqual({v.violation_id for v in report.apartment_violations}, {"1", "3"})

    def test_a_malformed_count_row_is_ignored_not_fatal(self):
        report = self._report(open_counts=[{"class": "C", "n": "x"}, "junk", {"class": "B", "n": "2"}])

        self.assertEqual(report.open_counts["B"], 2)
        self.assertEqual(report.open_counts["C"], 0)

    def test_violation_reports_are_cached_per_building(self):
        patcher, calls = fake_network()
        with patcher:
            bs.lookup("231 Echo Place")
            bs.lookup("231 Echo Place", "4B")

        data_calls = [c for c in calls if c[0] == bs.VIOLATIONS_URL]
        self.assertEqual(len(data_calls), 3, "one list + two aggregates, fetched once")


class DataDateHonestyTests(_CacheIsolation):
    def test_the_as_of_date_is_the_citys_not_ours(self):
        patcher, _ = fake_network()
        with patcher:
            report = bs.lookup("231 Echo Place")

        self.assertEqual(report.data_as_of, date(2026, 9, 27))

    def test_an_unavailable_date_is_none_never_today(self):
        """Guessing today would tell a tenant the data is fresher than we
        know it to be."""
        patcher, _ = fake_network(fail_on=lambda url, p: url == bs.VIOLATIONS_METADATA_URL)
        with patcher:
            report = bs.lookup("231 Echo Place")

        self.assertIsNone(report.data_as_of)

    def test_a_garbage_timestamp_is_none(self):
        patcher, _ = fake_network(metadata={"rowsUpdatedAt": "soon"})
        with patcher:
            self.assertIsNone(bs.lookup("231 Echo Place").data_as_of)


class NetworkFailureTests(_CacheIsolation):
    def test_transport_errors_become_lookup_unavailable(self):
        for exc in (urllib.error.URLError("down"), TimeoutError(), OSError("reset")):
            with self.subTest(exc=type(exc).__name__):
                with mock.patch.object(bs.urllib.request, "urlopen", side_effect=exc):
                    with self.assertRaises(bs.LookupUnavailable):
                        bs._get_json(bs.GEOSEARCH_URL, {"text": "x"})

    def test_a_non_json_body_becomes_lookup_unavailable(self):
        class Resp:
            def read(self):
                return b"<html>502</html>"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch.object(bs.urllib.request, "urlopen", return_value=Resp()):
            with self.assertRaises(bs.LookupUnavailable):
                bs._get_json(bs.VIOLATIONS_URL)

    def test_the_open_data_token_is_never_sent_to_geosearch(self):
        seen = {}

        class Resp:
            def read(self):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def urlopen(request, timeout=None):
            seen[request.full_url.split("?")[0]] = {k.lower(): v for k, v in request.headers.items()}
            return Resp()

        with mock.patch.dict(os.environ, {"NYC_OPEN_DATA_APP_TOKEN": "secret-token"}), \
                mock.patch.object(bs.urllib.request, "urlopen", side_effect=urlopen):
            bs._get_json(bs.GEOSEARCH_URL, {"text": "x"})
            bs._get_json(bs.VIOLATIONS_URL, {"$limit": "1"})

        self.assertNotIn("x-app-token", seen[bs.GEOSEARCH_URL])
        self.assertEqual(seen[bs.VIOLATIONS_URL]["x-app-token"], "secret-token")


class PresentationTests(unittest.TestCase):
    def test_citations_are_extracted_in_tenant_readable_form(self):
        self.assertEqual(
            bs.extract_citations("§ 27-2005 ADM CODE & 309 M/D LAW ABATE"),
            ["NYC Admin Code § 27-2005", "Multiple Dwelling Law § 309"],
        )
        self.assertEqual(bs.extract_citations("§ 27-2017.1 ADM CODE X"), ["NYC Admin Code § 27-2017.1"])
        self.assertEqual(bs.extract_citations("NO CITATION"), [])

    def test_descriptions_drop_the_shouting_and_the_citation_clause(self):
        text = bs.readable_description(ROW_C_OLDER["novdescription"])

        self.assertEqual(text, "Abate the nuisance consisting of mice")

    def test_unit_identifiers_stay_upper_case_and_ordinals_stay_lower(self):
        text = bs.readable_description(ROW_C_HEAT["novdescription"])

        self.assertIn("apt 4B", text)
        self.assertIn("4th story", text)

    def test_an_empty_description_stays_empty(self):
        self.assertEqual(bs.readable_description(""), "")


class ChatHandOffTests(_CacheIsolation):
    def _report(self, **kwargs):
        apartment = kwargs.pop("apartment", None)
        patcher, _ = fake_network(**kwargs)
        with patcher:
            return bs.lookup("231 Echo Place", apartment)

    def test_the_prompt_carries_the_facts_and_the_date(self):
        prompt = bs.chat_prompt(self._report())

        self.assertIn("231 ECHO PLACE, Bronx", prompt)
        self.assertIn("as of 2026-09-27", prompt)
        self.assertIn("4 open violations", prompt)
        self.assertIn("2 Class C", prompt)
        self.assertIn("NYC Admin Code § 27-2029", prompt)
        self.assertIn("certified as corrected", prompt)

    def test_the_prompt_leads_with_the_tenants_own_apartment(self):
        prompt = bs.chat_prompt(self._report(apartment="4B"))

        self.assertIn("apartment 4B", prompt)
        self.assertIn("In my apartment:", prompt)

    def test_a_clean_building_is_described_honestly(self):
        prompt = bs.chat_prompt(self._report(open_rows=[], open_counts=[{"class": "C", "n": "0"}]))

        self.assertIn("no open violations", prompt)

    def test_the_prompt_fits_inside_the_chat_message_cap(self):
        from routes import MAX_MESSAGE_LENGTH

        huge = dict(ROW_C_HEAT, novdescription="§ 27-2029 ADM CODE " + "PROVIDE HEAT " * 200)
        prompt = bs.chat_prompt(self._report(open_rows=[huge] * 10))

        self.assertLessEqual(len(prompt), bs.CHAT_PROMPT_MAX_CHARS)
        self.assertLess(bs.CHAT_PROMPT_MAX_CHARS, MAX_MESSAGE_LENGTH)


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------


class FakeAI:
    def is_ready(self):
        return True


def _client(logged_in=False):
    app = flask.Flask(__name__, template_folder=_TEMPLATES)
    app.secret_key = "t"
    app.config["SUPABASE_SERVICE"] = object()
    app.config["AI_SERVICE"] = FakeAI()
    app.register_blueprint(main_bp)
    configure_test_app(app)
    client = app.test_client()
    if logged_in:
        with client.session_transaction() as s:
            s["user_id"] = "u1"
            s["user_email"] = "t@example.com"
            s["access_token"] = "a"
    return client


class RouteTests(_CacheIsolation):
    def _get(self, query="", logged_in=False, **network):
        patcher, _ = fake_network(**network)
        with patcher:
            return _client(logged_in).get("/building" + query)

    def test_the_empty_form_renders_logged_out(self):
        response = self._get()

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('name="address"', body)
        self.assertIn("no account needed", body)

    def test_a_lookup_works_without_logging_in(self):
        response = self._get("?address=231+echo+pl+bx")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("231 ECHO PLACE, Bronx, NY, USA", body)
        self.assertIn("City data as of September 27, 2026", body)
        self.assertIn("NYC Admin Code § 27-2029", body)
        self.assertIn("Immediately hazardous", body)
        self.assertIn("Certified by landlord", body)
        self.assertIn("Rent impairing", body)

    def test_the_certified_explanation_appears_when_it_applies(self):
        body = self._get("?address=231+echo+pl").get_data(as_text=True)

        self.assertIn("certified as fixed by the landlord", body)
        self.assertIn("challenge it and HPD will re-inspect", body)

    def test_a_clean_building_does_not_claim_nothing_is_wrong(self):
        body = self._get(
            "?address=231+echo+pl", open_rows=[], open_counts=[{"class": "C", "n": "0"}]
        ).get_data(as_text=True)

        self.assertIn("mean nothing is wrong", body)

    def test_city_text_is_escaped(self):
        """novdescription comes from a third party and lands in our HTML."""
        hostile = dict(ROW_C_HEAT, novdescription='<script>alert(1)</script><img src=x onerror=alert(1)>')
        body = self._get("?address=231+echo+pl", open_rows=[hostile]).get_data(as_text=True)

        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertNotIn("<img src=x", body)
        self.assertIn("&lt;script&gt;", body)

    def test_a_hostile_label_is_escaped_in_the_title_and_the_prompt_attribute(self):
        evil = {"features": [{"properties": {
            "label": '"><script>alert(1)</script>', "addendum": {"pad": {"bbl": BBL}}}}]}
        body = self._get("?address=231+echo+pl", geocode=evil).get_data(as_text=True)

        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertNotIn('data-prompt="I live at "><script>', body)

    def test_invalid_input_is_400_with_a_message(self):
        response = self._get("?address=Echo+Place")

        self.assertEqual(response.status_code, 400)
        self.assertIn("Include the building number", response.get_data(as_text=True))

    def test_not_found_is_404_with_a_message(self):
        response = self._get("?address=99999+nowhere", geocode={"features": []})

        self.assertEqual(response.status_code, 404)
        self.assertTrue("couldn't find that building" in html.unescape(response.get_data(as_text=True)))

    def test_a_city_outage_is_503_and_still_a_real_page(self):
        response = self._get("?address=231+echo+pl", fail_on=lambda url, p: url == bs.VIOLATIONS_URL)

        self.assertEqual(response.status_code, 503)
        body = html.unescape(response.get_data(as_text=True))
        self.assertTrue("didn't respond" in body)
        self.assertIn('name="address"', body, "the search form must still be there")

    def test_an_unexpected_error_is_a_friendly_500_without_a_traceback(self):
        with mock.patch.object(bs, "lookup", side_effect=KeyError("boom")):
            response = _client().get("/building?address=231+echo+pl")

        self.assertEqual(response.status_code, 500)
        body = response.get_data(as_text=True)
        self.assertIn("Something went wrong", body)
        self.assertNotIn("Traceback", body)
        self.assertNotIn("KeyError", body)

    def test_logged_out_cta_points_at_signup_and_carries_the_prompt(self):
        body = self._get("?address=231+echo+pl").get_data(as_text=True)

        self.assertIn('href="/signup"', body)
        self.assertIn("Create a free account to talk this through", body)
        self.assertIn('data-prompt="I live at 231 ECHO PLACE', body)

    def test_logged_in_cta_creates_a_conversation_with_csrf(self):
        body = self._get("?address=231+echo+pl", logged_in=True).get_data(as_text=True)

        self.assertIn('action="/conversations"', body)
        self.assertIn('name="csrf_token"', body)
        self.assertIn("Talk this through", body)

    def test_the_apartment_section_appears_when_asked(self):
        body = self._get("?address=231+echo+pl&apt=4b").get_data(as_text=True)

        self.assertIn("In apartment 4B", body)

    def test_public_page_carries_the_footer_disclaimer_but_not_the_chat_banner(self):
        """The top banner tells people to message the chatbot, which a
        logged-out visitor cannot do -- same rule as the other public pages."""
        import branding

        body = self._get("?address=231+echo+pl").get_data(as_text=True)

        self.assertIn(branding.SHORT_DISCLAIMER, body)
        self.assertNotIn('class="top-disclaimer"', body)

    def test_the_route_is_rate_limited(self):
        import inspect

        import routes

        source = inspect.getsource(routes)
        route_at = source.index('@main_bp.route("/building")')
        self.assertIn("@_building_lookup_limit", source[route_at:route_at + 120])
        limit_at = source.index("_building_lookup_limit = limiter.shared_limit(")
        block = source[limit_at:source.index(")", source.index("exempt_when", limit_at)) + 1]
        self.assertIn('"30 per minute"', block)
        self.assertIn('scope="building_lookup"', block)
        self.assertIn('methods=["GET", "HEAD"]', block)  # HEAD runs the whole view too


class FrontDoorTests(unittest.TestCase):
    """The lookup only helps if first-time visitors can find it."""

    def test_signup_and_login_link_to_the_lookup(self):
        for path in ("/signup", "/login"):
            with self.subTest(path=path):
                body = _client().get(path).get_data(as_text=True)
                self.assertIn('href="/building"', body)
                self.assertIn("No account needed", body)


class ChatContinuationTests(unittest.TestCase):
    class Sb:
        def build_user_scoped_client(self, t):
            return object()

        def list_conversations(self, c, archived=False):
            return []

    def _chat_body(self):
        app = flask.Flask(__name__, template_folder=_TEMPLATES)
        app.secret_key = "t"
        app.config["SUPABASE_SERVICE"] = self.Sb()
        app.config["AI_SERVICE"] = FakeAI()
        app.register_blueprint(main_bp)
        configure_test_app(app)
        client = app.test_client()
        with client.session_transaction() as s:
            s["user_id"] = "u1"
            s["user_email"] = "t@example.com"
            s["access_token"] = "a"
        return client.get("/chat").get_data(as_text=True)

    def test_the_continue_chip_exists_and_starts_hidden(self):
        body = self._chat_body()

        self.assertIn("data-continue-form hidden", body)
        self.assertIn('sessionStorage.getItem("pendingChatTitle")', body)

    def test_the_empty_state_links_to_the_lookup(self):
        body = self._chat_body()
        self.assertIn('href="/"', body)  # street search, the default lookup since 2026-10-07
        self.assertIn('href="/resources"', body)

    def test_plain_new_chat_clears_a_stale_pending_prompt(self):
        """Without this, a prompt stored on the building page and never
        used would auto-send into the next unrelated chat."""
        body = self._chat_body()

        self.assertIn('document.querySelectorAll(".new-chat-form")', body)
        self.assertIn('sessionStorage.removeItem("pendingChatPrompt")', body)


if __name__ == "__main__":
    unittest.main()


class StatusLabelTests(unittest.TestCase):
    """'NOV SENT OUT' title-cased to 'Nov Sent Out', which sat beside an
    inspection date and read as the month of November."""

    def test_known_statuses_read_as_plain_english(self):
        self.assertEqual(bs.readable_status("NOV SENT OUT"), "Notice sent to owner")
        self.assertEqual(bs.readable_status("NOV CERTIFIED LATE"), "Owner certified fixed (late)")
        self.assertEqual(bs.readable_status("NOT COMPLIED WITH"), "Not complied with")

    def test_no_status_ever_renders_as_nov(self):
        for raw in ("NOV SENT OUT", "SOME FUTURE NOV STATUS", "INFO NOV SENT OUT", "NOV"):
            with self.subTest(raw=raw):
                self.assertNotRegex(bs.readable_status(raw), r"\\bNov\\b|\\bnov\\b")

    def test_unknown_statuses_spell_out_the_abbreviation(self):
        self.assertEqual(bs.readable_status("SOME FUTURE NOV STATUS"), "Some future notice of violation status")

    def test_a_word_merely_starting_with_nov_is_left_alone(self):
        self.assertEqual(bs.readable_status("NOVEL CONDITION"), "Novel condition")

    def test_whitespace_and_case_do_not_defeat_the_mapping(self):
        self.assertEqual(bs.readable_status("  nov   sent  out "), "Notice sent to owner")

    def test_blank_is_status_unknown(self):
        self.assertEqual(bs.readable_status(""), "Status unknown")

    def test_the_rendered_page_never_says_nov(self):
        patcher, _ = fake_network()
        bs.clear_caches()
        with patcher:
            body = _client().get("/building?address=231+echo+pl").get_data(as_text=True)
        bs.clear_caches()

        self.assertNotIn("Nov Sent Out", body)
        self.assertIn("Notice sent to owner", body)


class OnAccentContrastTests(unittest.TestCase):
    """White text on the dark-mode accent (#5b9bd5) measured 2.96:1, under
    WCAG AA's 4.5:1 -- on every filled button on every themed page, not
    just this one. Text on an accent fill now uses --on-accent, which is
    white in light mode and near-black in dark (6.05:1)."""

    THEMED = ("chat.html", "settings.html", "learn_more.html", "building.html", "law.html")

    @staticmethod
    def _ratio(a, b):
        def lum(h):
            h = h.lstrip("#")
            r, g, bl = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
            f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
            return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(bl)
        hi, lo = sorted((lum(a), lum(b)), reverse=True)
        return (hi + 0.05) / (lo + 0.05)

    def _css(self, name):
        with open(os.path.join(_TEMPLATES, name), encoding="utf-8") as fh:
            source = fh.read()
        return source[source.index("<style>"):source.index("</style>")]

    def test_no_themed_page_puts_hardcoded_white_on_the_accent(self):
        import re

        for name in self.THEMED:
            with self.subTest(template=name):
                self.assertIsNone(re.search(r"color:\\s*#fff\\b", self._css(name)))

    def test_every_theme_block_defines_on_accent(self):
        for name in self.THEMED:
            with self.subTest(template=name):
                self.assertEqual(self._css(name).count("--on-accent:"), 3, "light + both dark blocks")

    def test_the_chosen_colours_pass_wcag_aa_in_both_themes(self):
        self.assertGreaterEqual(self._ratio("#ffffff", "#1c5d8c"), 4.5)   # light: on accent
        self.assertGreaterEqual(self._ratio("#101820", "#5b9bd5"), 4.5)   # dark: on accent
        self.assertGreaterEqual(self._ratio("#101820", "#7db2e0"), 4.5)   # dark: on accent-hover


class RealHpdDescriptionTests(unittest.TestCase):
    """Run against real HPD violation text, not idealised strings.

    The first version of the citation parser was written against 2014-era
    records and it showed. On 5,000 current open violations it:
      - put a FALSE "NYC Admin Code" label on 1,348 of them, by labelling
        city RULES ("28 RCNY § 11-06") as the statute;
      - left leading punctuation on 1,554 cleaned descriptions;
      - left "Hmc:"/"§" residue at the front of 1,273.
    tests/fixtures/hpd_violation_descriptions.json holds real strings
    covering every citation-clause shape seen in that sample (42 shapes).
    These tests hold the fixed parser to zero on all of them.
    """

    @classmethod
    def setUpClass(cls):
        import json

        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "hpd_violation_descriptions.json")
        with open(path, encoding="utf-8") as fh:
            cls.descriptions = json.load(fh)["descriptions"]

    def test_the_fixture_is_substantial(self):
        self.assertGreaterEqual(len(self.descriptions), 100)

    def test_no_rule_is_ever_labelled_as_the_statute(self):
        import re

        for d in self.descriptions:
            for c in bs.extract_citations(d):
                if c.startswith("NYC Admin Code"):
                    number = c.split("§ ")[1]
                    in_hmc = re.fullmatch(r"27-2\d{3}(\.\d+)?", number)
                    named = re.search(re.escape(number) + r"[^;:]{0,40}?(HMC|ADM(IN)?\.?\s*CODE)", d, re.I)
                    with self.subTest(description=d[:80], citation=c):
                        self.assertTrue(in_hmc or named, "Admin Code label without HMC number or explicit naming")
                        self.assertNotRegex(d, re.escape(number) + r"\s*,?\s*RCNY")

    def test_cleaned_text_never_starts_with_punctuation_or_citation_residue(self):

        for d in self.descriptions:
            text = bs.readable_description(d)
            with self.subTest(description=d[:80]):
                self.assertTrue(text)
                self.assertNotRegex(text, r"^[\W_]")
                self.assertNotRegex(text, r"(?i)^(hmc|adm|admin|m/d|mdl|rcny)\b")
                self.assertNotRegex(text, r"[\x00-\x1f]")

    def test_every_hmc_section_number_is_found(self):
        import re

        for d in self.descriptions:
            cites = " ".join(bs.extract_citations(d))
            for number in re.findall(r"27-2\d{3}(?:\.\d+)?", d):
                with self.subTest(description=d[:80], number=number):
                    self.assertIn(number, cites)

    def test_mdl_sections_are_plausible(self):
        """Multiple Dwelling Law sections are inferred from bare numbers, so
        they get their own sanity bound -- the MDL runs to roughly 370."""
        for d in self.descriptions:
            for c in bs.extract_citations(d):
                if c.startswith("Multiple Dwelling Law"):
                    n = int("".join(ch for ch in c.split("§ ")[1] if ch.isdigit()))
                    with self.subTest(citation=c):
                        self.assertTrue(1 <= n <= 370)


class KnownHpdShapesTests(unittest.TestCase):
    """One assertion per notable real shape, so a failure names the shape."""

    def test_rule_numbers_after_the_statute_are_labelled_as_rules(self):
        self.assertEqual(
            bs.extract_citations("§ 27-2005(B)(2)(B) HMC, § 11-52, § 11-53 RCNY REPAIR X"),
            ["NYC Admin Code § 27-2005", "NYC Rules (RCNY) § 11-52", "NYC Rules (RCNY) § 11-53"],
        )

    def test_a_titled_rule_keeps_its_title(self):
        self.assertIn("28 RCNY § 25-171", bs.extract_citations("28 RCNY § 25-171; & 67 (7)(B) MDL; NYC FIRE CODE § 703.1.3: ADJUST DOOR"))

    def test_fire_code_is_not_labelled_as_anything(self):
        cites = bs.extract_citations("28 RCNY § 25-171; & 67 (7)(B) MDL; NYC FIRE CODE § 703.1.3: ADJUST DOOR")
        self.assertFalse(any("703" in c for c in cites))
        self.assertIn("Multiple Dwelling Law § 67", cites)

    def test_later_numbers_in_a_run_need_no_section_sign(self):
        self.assertIn("NYC Rules (RCNY) § 12-10", bs.extract_citations("§27-2045(B)(5) HMC, § 12-06, 12-10 RCNY POST A NOTICE"))

    def test_hmc_prefix_before_the_section_sign(self):
        text = "HMC ADM CODE: § 27-2017.4 ABATE THE INFESTATION CONSISTING OF ROACHES"
        self.assertEqual(bs.extract_citations(text), ["NYC Admin Code § 27-2017.4"])
        self.assertEqual(bs.readable_description(text), "Abate the infestation consisting of roaches")

    def test_trailing_hmc_colon(self):
        self.assertEqual(
            bs.readable_description("§ 27-2005 HMC: PROPERLY REPAIR THE BROKEN SINK"),
            "Properly repair the broken sink",
        )

    def test_dash_separator(self):
        self.assertEqual(
            bs.readable_description("§ 27-2056.6 ADM CODE - CORRECT THE LEAD-BASED PAINT HAZARD"),
            "Correct the lead-based paint hazard",
        )

    def test_explicitly_named_admin_code_outside_the_hmc(self):
        self.assertEqual(bs.extract_citations("§ 26-1103 ADMIN. CODE: POST AND MAINTAIN A NOTICE"), ["NYC Admin Code § 26-1103"])

    def test_control_characters_are_removed(self):
        self.assertEqual(bs.readable_description("§ 27-2005 HMC: REFIT\x1a DOOR"), "Refit door")

    def test_mdl_only_clause(self):
        self.assertEqual(bs.extract_citations("§ 300 M/D LAW FILE PLANS"), ["Multiple Dwelling Law § 300"])

    def test_bare_numbers_in_the_body_are_never_mdl(self):
        cites = bs.extract_citations("§ 27-2005 ADM CODE & 309 M/D LAW REPAIR AT APT 5, 3rd STORY, ROOM 12")
        self.assertEqual(cites, ["NYC Admin Code § 27-2005", "Multiple Dwelling Law § 309"])


class OrClauseTests(unittest.TestCase):
    """Measured live: a building whose violations all lack bbl showed 0 open
    violations with a bbl-only query and 138 (9 Class C) with this one."""

    def test_the_filter_matches_bbl_or_hpds_own_block_and_lot(self):
        self.assertEqual(
            bs._building_where("2059111102"),
            "(bbl='2059111102' OR (boroid='2' AND block='5911' AND lot='1102'))",
        )

    def test_block_and_lot_are_unpadded_the_way_hpd_stores_them(self):
        self.assertIn("block='2810' AND lot='45'", bs._building_where("2028100045"))

    def test_every_query_uses_the_widened_filter(self):
        patcher, calls = fake_network()
        bs.clear_caches()
        with patcher:
            bs.lookup("231 Echo Place")
        bs.clear_caches()
        data_wheres = [p.get("$where", "") for url, p in calls if url == bs.VIOLATIONS_URL]
        self.assertEqual(len(data_wheres), 3)
        for where in data_wheres:
            with self.subTest(where=where):
                self.assertIn("OR (boroid=", where)

    def test_the_widened_filter_still_refuses_a_malformed_bbl(self):
        with self.assertRaises(ValueError):
            bs._building_where("2028100045' OR '1'='1")
