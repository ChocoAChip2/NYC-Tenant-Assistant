"""'Did you mean' for the street and address searches (street_suggestions.py).

The fake city below answers the way the live services did on 2026-10-11:
the geocoder always returns something ("231 Ecco Place, Bronx" comes back
as 231 BROWN PLACE first), labels everything "fallback", and knows alias
spellings ("GR CONCOURSE") that no tax lot uses. Nothing here touches the
network: an unexpected URL fails the test.
"""

import html
import os
import unittest
from unittest import mock

import flask

import area_service
import building_service as bs
import street_suggestions as ss
from routes import main_bp
from rate_limit import limiter
from tests.app_test_support import configure_test_app

_TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")

STREETS = {
    "Bronx": ["ECHO PLACE", "BROWN PLACE", "MEAD STREET", "WESTCHESTER AVENUE", "WESTCHESTER AVE", "WESTCHESTER SQUARE",
              "GRAND CONCOURSE", "GR CONCOURSE", "EAST 149 STREET", "EAST 148 STREET", "O'BRIEN AVENUE",
              "<SCRIPT>ALERT(1)</SCRIPT> PLACE"],
    "Brooklyn": ["TECH PLACE", "FLATBUSH AVENUE"],
    "Queens": ["37 AVENUE", "STEINWAY STREET"],
    "Manhattan": ["BROADWAY"],
    "Staten Island": ["ECHO PLACE"],
}
# Streets with tax lots. "GR CONCOURSE" is known to the geocoder but has none.
WITH_LOTS = {("Bronx", "ECHO PLACE"), ("Bronx", "BROWN PLACE"), ("Bronx", "WESTCHESTER AVENUE"),
             ("Bronx", "GRAND CONCOURSE"), ("Bronx", "EAST 149 STREET"), ("Bronx", "O'BRIEN AVENUE"),
             ("Brooklyn", "TECH PLACE"), ("Queens", "STEINWAY STREET"), ("Queens", "37 AVENUE")}
BUILDINGS = {
    ("Bronx", "ECHO PLACE"): {"231": "2028100045", "179": "2028080021"},
    ("Bronx", "BROWN PLACE"): {"231": "2022820043"},
    ("Queens", "37 AVENUE"): {"31-10": "4003730021"},
    ("Queens", "STEINWAY STREET"): {"31-10": "4006510001"},
    ("Staten Island", "ECHO PLACE"): {"12": "5014940045"},
}
BOROID = {b: v[1] for b, v in area_service.BOROUGHS.items()}


def feature(number, street, borough, bbl):
    return {"properties": {
        "label": f"{number} {street}, {borough}, NY, USA", "housenumber": number, "street": street,
        "borough": borough, "match_type": "fallback", "addendum": {"pad": {"bbl": bbl, "bin": "2000001"}},
    }}


def _features_on(street, borough, first=None):
    numbers = dict(BUILDINGS.get((borough, street), {"100": "2000000001"}))
    ordered = ([first] if first in numbers else []) + [n for n in numbers if n != first]
    return [feature(n, street, borough, numbers[n]) for n in ordered]


def fake_city(fail_street_list=False, extra_geo=None):
    calls = []

    def geocoder(text):
        if extra_geo and text in extra_geo:
            return {"features": extra_geo[text]}
        upper = text.upper()
        typed = ss.parse_address(text)
        if typed.number is None:
            # A street search: "<words>, <borough>".
            name, _, where = upper.rpartition(", ")
            borough = next((b for b in area_service.BOROUGHS if b.upper() == where), None)
            if borough and name in STREETS[borough] and "<" not in name:
                return {"features": _features_on(name, borough)}
            return {"features": [feature("5", "O'CONNELL PLACE", borough or "Bronx", "2030000001")]}
        boroughs = [typed.borough] if typed.borough else list(area_service.BOROUGHS)
        for borough in boroughs:
            for street in STREETS[borough]:
                if area_service._matches(list(typed.words), street) and (borough, street) in BUILDINGS:
                    return {"features": _features_on(street, borough, typed.number)}
        if "ECCO" in upper:
            return {"features": [feature("231", "BROWN PLACE", "Bronx", "2022820043"),
                                 feature("231", "ECHO PLACE", "Bronx", "2028100045")]}
        return {"features": [feature(typed.number, "WEST 231 STREET", "Bronx", "2057060007")]}

    def get(url, params=None):
        params = params or {}
        calls.append((url, params))
        if url == ss.STREET_LIST_URL:
            if fail_street_list:
                raise bs.LookupUnavailable("street list down")
            boroid = params["$where"].split("'")[1]
            borough = next(b for b, code in BOROID.items() if code == boroid)
            return [{"streetname": name} for name in STREETS[borough]] + [{"streetname": None}, "junk"]
        if url == bs.GEOSEARCH_URL:
            return geocoder(params["text"])
        if url == area_service.PLUTO_URL:
            where = params["$where"]
            for (borough, street) in WITH_LOTS:
                code = area_service.BOROUGHS[borough][0]
                if f"borough='{code}'" in where and where.endswith(f"'% {street.replace(chr(39), chr(39) * 2)}'"):
                    return [{"bbl": "2028100045", "address": f"231 {street}", "zipcode": "10457"}]
            return []
        if url == area_service.COMMUNITY_BOARDS_URL:
            return []
        if any(url == layer for layer, _ in area_service.DISTRICT_LAYERS.values()):
            return {"features": []}
        if url == bs.VIOLATIONS_METADATA_URL:
            return {"rowsUpdatedAt": 1790000000}
        if url == bs.VIOLATIONS_URL:
            return []
        raise AssertionError(f"unexpected URL {url}")

    return get, calls


class _Case(unittest.TestCase):
    def setUp(self):
        # The limiter is shared by the whole run; reset() asserts if no test attached it yet.
        if getattr(limiter, "_storage", None) is not None:
            limiter.reset()
        for module in (bs, area_service, ss):
            module.clear_caches()
            self.addCleanup(module.clear_caches)

    def city(self, **options):
        get, calls = fake_city(**options)
        for module in (bs, area_service):
            patcher = mock.patch.object(module, "_get_json", get)
            patcher.start()
            self.addCleanup(patcher.stop)
        return calls

    def client(self):
        app = flask.Flask(__name__, template_folder=_TEMPLATES)
        app.secret_key = "t"
        app.config["SUPABASE_SERVICE"] = object()
        app.config["AI_SERVICE"] = type("AI", (), {"is_ready": lambda self: True})()
        app.register_blueprint(main_bp)
        configure_test_app(app)
        return app.test_client()

    def suggest_street(self, raw, borough):
        with self.assertRaises(area_service.StreetNotFound) as caught:
            area_service.resolve_street(raw, borough)
        return [(s.street, s.borough, s.other_borough) for s in ss.for_street(raw, borough, caught.exception.returned)]


class ReadingAnAddressTests(unittest.TestCase):
    def test_number_street_and_borough(self):
        cases = {
            "231 echo pl, bx": ("231", ("ECHO", "PLACE"), "Bronx"),
            "231 echo pl bx": ("231", ("ECHO", "PLACE"), "Bronx"),
            "231 Echo Place, Bronx, NY 10457": ("231", ("ECHO", "PLACE"), "Bronx"),
            "231 Echo Place 10457": ("231", ("ECHO", "PLACE"), "Bronx"),
            "31-10 37 Ave, Queens": ("31-10", ("37", "AVENUE"), "Queens"),
            "12A Echo Place Staten Island": ("12A", ("ECHO", "PLACE"), "Staten Island"),
            "350 5th Ave, New York, NY 10118": ("350", ("5", "AVENUE"), "Manhattan"),
            "100 Queens Blvd": ("100", ("QUEENS", "BOULEVARD"), None),
            "5 Bronx Park East": ("5", ("BRONX", "PARK", "EAST"), None),
            "100 Manhattan Ave Brooklyn": ("100", ("MANHATTAN", "AVENUE"), "Brooklyn"),
            "10 Bronx": ("10", ("BRONX",), None),
            "231 E. 149th St, the Bronx": ("231", ("EAST", "149", "STREET"), "Bronx"),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                typed = ss.parse_address(text)
                self.assertEqual((typed.number, typed.words, typed.borough), expected)

    def test_text_that_does_not_start_with_a_number(self):
        self.assertIsNone(ss.parse_address("Echo Place 231, Bronx").number)
        self.assertEqual(ss.parse_address("Apt 4, Bronx").borough, "Bronx")
        self.assertIsNone(ss.parse_address("").number)

    def test_house_numbers_compare_after_normalizing(self):
        self.assertTrue(ss.same_number("31-10", "3110"))
        self.assertTrue(ss.same_number("31-10", "31-10"))
        self.assertTrue(ss.same_number("12a", "12A"))
        self.assertTrue(ss.same_number("12 A", "12A"))
        self.assertFalse(ss.same_number("12A", "12"))
        self.assertFalse(ss.same_number("1", "179"))
        self.assertFalse(ss.same_number("", ""))
        self.assertFalse(ss.same_number(None, "1"))

    def test_same_street(self):
        yes = [(("ECHO", "PLACE"), "ECHO PLACE"), (("ECHO",), "ECHO PLACE"), (("EAST", "149", "STREET"), "EAST 149 STREET"),
               (("BROADWAY", "ASTORIA"), "BROADWAY"), (("37", "AVENUE", "LONG", "ISLAND", "CITY"), "37 AVENUE"),
               # The geocoder echoes a spelled-out ordinal (live, 2026-10-11): "400 First
               # Avenue" came back as FIRST AVENUE and was refused as another street.
               (("1", "AVENUE"), "FIRST AVENUE"), (("5", "AVENUE"), "FIFTH AVENUE"), (("5", "AVENUE"), "5TH AVENUE"),
               (("WEST", "4", "STREET"), "WEST FOURTH STREET"), (("3", "AVENUE"), "3 AVENUE")]
        no = [(("ECCO", "PLACE"), "BROWN PLACE"), (("ECCO", "PLACE"), "ECHO PLACE"), (("EAST", "149", "STREET"), "WEST 149 STREET"),
              (("EAST", "149", "STREET"), "EAST 149 AVENUE"), (("ASDFGH", "STREET"), "WEST 231 STREET"), ((), "ECHO PLACE"),
              (("ECHO", "PLACE"), ""), (("PLACE", "ASTORIA"), "PLACE"), (("1", "AVENUE"), "FIRST PLACE"),
              (("EAST", "4", "STREET"), "WEST FOURTH STREET"), (("5", "AVENUE"), "SIXTH AVENUE")]
        for words, street in yes:
            with self.subTest(words=words, street=street):
                self.assertTrue(ss.same_street(words, street))
        for words, street in no:
            with self.subTest(words=words, street=street):
                self.assertFalse(ss.same_street(words, street))

    def test_a_result_without_street_fields_is_read_from_its_label(self):
        self.assertEqual(ss.feature_address({"label": "231 ECHO PLACE, Bronx, NY, USA"}), ("231", "ECHO PLACE"))
        self.assertEqual(ss.feature_address({"label": "Echo Park"}), ("", ""))
        self.assertEqual(ss.feature_address({"housenumber": "31-10", "street": "37 avenue"}), ("31-10", "37 AVENUE"))


class SimilarityTests(unittest.TestCase):
    def test_the_measured_typos_are_close_and_nonsense_is_not(self):
        words = area_service.street_words
        self.assertGreaterEqual(ss.similarity(words("Ecco Place"), "ECHO PLACE"), ss.MIN_SIMILARITY)
        self.assertGreater(ss.similarity(words("Westchster Ave"), "WESTCHESTER AVENUE"), 0.9)
        self.assertGreater(ss.similarity(words("Grand Concorse"), "GRAND CONCOURSE"), 0.9)
        self.assertEqual(ss.similarity(words("Asdfgh Street"), "MEAD STREET"), 0.0)
        self.assertEqual(ss.similarity(words("Asdfgh Street"), "ECHO PLACE"), 0.0)

    def test_a_shared_street_type_does_not_inflate_the_score(self):
        self.assertEqual(ss.similarity(["ZZZZ", "AVENUE"], "WESTCHESTER AVENUE"), 0.0)
        self.assertEqual(ss.similarity(["QQQQQQ", "PLACE"], "ECHO PLACE"), 0.0)

    def test_a_typed_type_direction_or_number_is_never_corrected(self):
        self.assertEqual(ss.similarity(["WESTCHSTER", "AVENUE"], "WESTCHESTER SQUARE"), 0.0)
        self.assertEqual(ss.similarity(["EAST", "149", "STREET"], "WEST 149 STREET"), 0.0)
        self.assertEqual(ss.similarity(["EAST", "149", "STREET"], "EAST 148 STREET"), 0.0)
        self.assertEqual(ss.similarity(["ECHO", "PLAZA"], "ECHO PLACE"), 0.0)

    def test_short_names_need_a_closer_match(self):
        self.assertEqual(ss.similarity(["OAK", "STREET"], "ASH STREET"), 0.0)
        self.assertEqual(ss.similarity(["ELM", "STREET"], "ELK STREET"), 0.0)

    def test_closest_is_best_first_and_prefers_what_the_geocoder_also_offered(self):
        names = ["ECHO PLACE", "TECH PLACE", "BROWN PLACE"]
        self.assertEqual(ss.closest(["ECCO", "PLACE"], names), ["ECHO PLACE"])
        self.assertEqual(ss.closest(["ECHO", "PLACE"], names), ["ECHO PLACE", "TECH PLACE"])
        self.assertEqual(ss.closest(["TECO", "PLACE"], names, also_returned=["TECH PLACE"])[0], "TECH PLACE")


class StreetListTests(_Case):
    def test_one_fetch_per_borough_normalized_and_deduped(self):
        calls = self.city()
        names = ss.borough_streets("Bronx")
        self.assertIn("WESTCHESTER AVENUE", names)
        self.assertEqual(names.count("WESTCHESTER AVENUE"), 1, "AVE and AVENUE collapse")
        self.assertNotIn("WESTCHESTER AVE", names)
        self.assertFalse([n for n in names if "<" in n], "unsafe names are dropped")
        self.assertIn("37 AVENUE", ss.borough_streets("Queens"), "a numbered street keeps its number")
        ss.borough_streets("Bronx")
        ss.borough_streets("Queens")
        fetches = [p for url, p in calls if url == ss.STREET_LIST_URL]
        self.assertEqual(len(fetches), 2)
        self.assertEqual(fetches[0]["$where"], "boroid='2'")
        self.assertEqual(fetches[0]["$group"], "streetname")

    def test_a_failed_fetch_is_not_retried_for_every_borough(self):
        calls = self.city(fail_street_list=True)
        self.assertEqual(self.suggest_street("Ecco Place", "Bronx"), [])
        self.assertEqual(len([1 for url, _ in calls if url == ss.STREET_LIST_URL]), 1)
        self.assertEqual(ss.for_street("Westchster Ave", "Bronx"), [])
        self.assertEqual(len([1 for url, _ in calls if url == ss.STREET_LIST_URL]), 1)

    def test_an_unexpected_shape_means_no_suggestions(self):
        with mock.patch.object(area_service, "_get_json", lambda url, params=None: {"error": "nope"}):
            self.assertEqual(ss.for_street("Ecco Place", "Bronx"), [])


class StreetSuggestionTests(_Case):
    def test_measured_typos(self):
        self.city()
        self.assertEqual(self.suggest_street("Ecco Place", "Bronx"), [("ECHO PLACE", "Bronx", False)])
        self.assertEqual(self.suggest_street("Westchster Ave", "Bronx"), [("WESTCHESTER AVENUE", "Bronx", False)])
        self.assertEqual(self.suggest_street("Grand Concorse", "Bronx"), [("GRAND CONCOURSE", "Bronx", False)])

    def test_nonsense_gets_no_suggestions(self):
        self.city()
        self.assertEqual(self.suggest_street("Asdfgh Street", "Bronx"), [])
        self.assertEqual(self.suggest_street("Asdfgh Street", "Queens"), [])

    def test_the_right_name_in_the_wrong_borough_offers_the_right_borough_first(self):
        self.city()
        suggestions = self.suggest_street("Echo Place", "Brooklyn")
        self.assertEqual(suggestions[0], ("ECHO PLACE", "Bronx", True))
        self.assertIn(("TECH PLACE", "Brooklyn", False), suggestions)
        self.assertNotIn(("ECHO PLACE", "Staten Island", True), suggestions, "listed there, but has no buildings to show")

    def test_an_alias_with_no_buildings_is_never_offered(self):
        self.city()
        self.assertIn("GR CONCOURSE", ss.borough_streets("Bronx"))
        self.assertEqual(area_service.resolve_street("GR CONCOURSE", "Bronx"), "GR CONCOURSE")
        suggestions = self.suggest_street("Grand Concorse", "Bronx")
        self.assertNotIn(("GR CONCOURSE", "Bronx", False), suggestions)

    def test_at_most_three_and_a_bounded_number_of_confirmations(self):
        many = [f"ECH{c} PLACE" for c in "ABCDEFGHIJ"]
        with mock.patch.dict(STREETS, {"Bronx": many}), \
             mock.patch.object(ss, "_confirmed_street", side_effect=lambda name, borough, get_json=None: name) as confirm:
            self.city()
            self.assertEqual(len(ss.for_street("Echx Place", "Bronx")), ss.MAX_SUGGESTIONS)
        self.assertLessEqual(confirm.call_count, ss.MAX_CONFIRMATIONS)
        with mock.patch.dict(STREETS, {"Bronx": many}), \
             mock.patch.object(ss, "_confirmed_street", return_value=None) as confirm:
            ss.clear_caches()
            self.assertEqual(ss.for_street("Echx Place", "Bronx"), [])
        self.assertEqual(confirm.call_count, ss.MAX_CONFIRMATIONS)

    def test_a_label_reads_like_a_place(self):
        self.assertEqual(ss.Suggestion("EAST 149 STREET", "Bronx").label, "East 149 Street, Bronx")
        self.assertEqual(ss.Suggestion("ECHO PLACE", "Bronx", house_number="231").address, "231 Echo Place, Bronx")
        self.assertIsNone(ss.Suggestion("ECHO PLACE", "Bronx").address)


class GeocodeChecksItsAnswerTests(_Case):
    def test_a_different_street_is_not_found_never_shown(self):
        self.city()
        with self.assertRaises(bs.AddressNotFound) as caught:
            bs.geocode("231 Ecco Place, Bronx")
        self.assertEqual(caught.exception.typed.number, "231")
        self.assertIn(("BROWN PLACE", "Bronx"), caught.exception.returned)

    def test_an_exact_match_has_nothing_to_say(self):
        self.city()
        match = bs.geocode("231 Echo Place, Bronx")
        self.assertEqual((match.bbl, match.house_number, match.street, match.requested),
                         ("2028100045", "231", "ECHO PLACE", None))
        self.assertIsNone(bs.geocode("31-10 37 Ave, Queens").requested)

    def test_an_exact_match_wins_even_when_it_is_not_listed_first(self):
        self.city(extra_geo={"231 Echo Place, Bronx": [feature("179", "ECHO PLACE", "Bronx", "2028080021"),
                                                      feature("231", "ECHO PLACE", "Bronx", "2028100045")]})
        match = bs.geocode("231 Echo Place, Bronx")
        self.assertEqual((match.house_number, match.requested), ("231", None))

    def test_a_spelled_out_ordinal_street_is_the_same_street(self):
        # Live 2026-10-11: "350 Fifth Avenue, Manhattan" and "400 First Avenue,
        # Manhattan" were 404s offering "350 5 Avenue" / "400 1 Avenue".
        self.city(extra_geo={"400 First Avenue, Manhattan": [feature("400", "FIRST AVENUE", "Manhattan", "1009490001")],
                             "100 West Fourth Street, Manhattan": [feature("50", "WEST FOURTH STREET", "Manhattan", "1005420001")]})
        match = bs.geocode("400 First Avenue, Manhattan")
        self.assertEqual((match.bbl, match.house_number, match.requested), ("1009490001", "400", None))
        match = bs.geocode("100 West Fourth Street, Manhattan")
        self.assertEqual((match.house_number, match.requested), ("50", "100 West Fourth Street"))

    def test_a_different_number_on_the_same_street_is_flagged(self):
        self.city()
        match = bs.geocode("1 Echo Place, Bronx")
        self.assertEqual(match.street, "ECHO PLACE")
        self.assertNotEqual(match.house_number, "1")
        self.assertEqual(match.requested, "1 Echo Place")

    def test_a_result_in_another_borough_is_not_used_when_a_borough_was_typed(self):
        self.city(extra_geo={"12A Echo Place Bronx": [feature("12", "ECHO PLACE", "Staten Island", "5014940045"),
                                                     feature("179", "ECHO PLACE", "Bronx", "2028080021")]})
        match = bs.geocode("12A Echo Place Bronx")
        self.assertEqual((match.borough, match.house_number, match.requested), ("Bronx", "179", "12A Echo Place"))

    def test_text_that_cannot_be_compared_behaves_as_before(self):
        self.city(extra_geo={"Echo Place 231": [feature("231", "ECHO PLACE", "Bronx", "2028100045")]})
        self.assertEqual(bs.geocode("Echo Place 231").bbl, "2028100045")

    def test_the_hand_off_does_not_claim_the_tenant_lives_at_a_substituted_building(self):
        self.city()
        self.assertTrue(bs.chat_prompt(bs.lookup("1 Echo Place, Bronx")).startswith("I looked up "))
        self.assertTrue(bs.chat_prompt(bs.lookup("231 Echo Place, Bronx")).startswith("I live at 231 ECHO PLACE"))


class AddressSuggestionTests(_Case):
    def suggest(self, text):
        with self.assertRaises(bs.AddressNotFound) as caught:
            bs.geocode(text)
        return [(s.address, s.street, s.borough) for s in ss.for_address(caught.exception.typed, caught.exception.returned)]

    def test_the_corrected_address_keeps_the_house_number(self):
        self.city()
        self.assertEqual(self.suggest("231 Ecco Place, Bronx"), [("231 Echo Place, Bronx", "ECHO PLACE", "Bronx")])
        self.assertEqual(self.suggest("31-10 Stienway St, Queens"), [("31-10 Steinway Street, Queens", "STEINWAY STREET", "Queens")])

    def test_only_an_address_that_geocodes_exactly_is_offered(self):
        self.city()
        suggestions = self.suggest("999 Ecco Place, Bronx")
        self.assertEqual(suggestions, [(None, "ECHO PLACE", "Bronx")], "no 999 there: the street alone is offered")

    def test_nonsense_and_uncomparable_text_get_nothing(self):
        self.city()
        self.assertEqual(self.suggest("231 Asdfgh Street, Bronx"), [])
        self.assertEqual(ss.for_address(None), [])
        self.assertEqual(ss.for_address(ss.parse_address("Echo Place, Bronx")), [])

    def test_a_typed_borough_is_the_only_one_searched(self):
        calls = self.city()
        self.suggest("231 Ecco Place, Bronx")
        self.assertEqual({p["$where"] for url, p in calls if url == ss.STREET_LIST_URL}, {"boroid='2'"})

    def test_a_failed_street_list_means_no_suggestions(self):
        self.city(fail_street_list=True)
        self.assertEqual(self.suggest("231 Ecco Place, Bronx"), [])


class AreaRouteTests(_Case):
    def get(self, query, **options):
        self.city(**options)
        response = self.client().get("/area?" + query)
        return response, response.get_data(as_text=True)

    def test_a_typo_is_still_404_with_a_link_to_the_right_street(self):
        response, body = self.get("street=Ecco+Place&borough=Bronx")
        self.assertEqual(response.status_code, 404)
        self.assertIn("couldn't find that street", html.unescape(body))
        self.assertIn("Did you mean", body)
        self.assertIn('<a href="/area?street=Echo+Place&amp;borough=Bronx">Echo Place, Bronx</a>', body)
        self.assertNotIn("A different borough", body)

    def test_the_home_page_search_gets_them_too(self):
        self.city()
        response = self.client().get("/?street=Westchster+Ave&borough=Bronx")
        self.assertEqual(response.status_code, 404)
        self.assertIn('href="/area?street=Westchester+Avenue&amp;borough=Bronx"', response.get_data(as_text=True))

    def test_the_wrong_borough_offers_the_right_one_and_says_so(self):
        response, body = self.get("street=Echo+Place&borough=Brooklyn&zip=11201")
        self.assertEqual(response.status_code, 404)
        self.assertIn('<a href="/area?street=Echo+Place&amp;borough=Bronx">Echo Place, Bronx</a><span class="where">A different borough</span>', body)
        self.assertIn('href="/area?street=Tech+Place&amp;borough=Brooklyn&amp;zip=11201"', body)

    def test_nonsense_is_a_plain_not_found(self):
        response, body = self.get("street=Asdfgh+Street&borough=Bronx")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("Did you mean", body)

    def test_a_failed_street_list_is_a_plain_not_found(self):
        response, body = self.get("street=Ecco+Place&borough=Bronx", fail_street_list=True)
        self.assertEqual(response.status_code, 404)
        self.assertIn("couldn't find that street", html.unescape(body))
        self.assertNotIn("Did you mean", body)

    def test_names_are_escaped(self):
        response, body = self.get("street=O%27Brian+Avenue&borough=Bronx")
        self.assertEqual(response.status_code, 404)
        self.assertIn('<a href="/area?street=O&#39;brien+Avenue&amp;borough=Bronx">O&#39;brien Avenue, Bronx</a>', body)
        self.assertNotIn("O'brien", body)
        self.assertNotIn("ALERT(1)", body.upper())

    def test_a_found_street_shows_no_suggestions(self):
        response, body = self.get("street=Echo+Place&borough=Bronx")
        self.assertNotIn("Did you mean", body)


class BuildingRouteTests(_Case):
    def get(self, query, **options):
        self.city(**options)
        response = self.client().get("/building?" + query)
        return response, response.get_data(as_text=True)

    def test_the_wrong_building_is_no_longer_shown(self):
        response, body = self.get("address=231+Ecco+Place%2C+Bronx&apt=4B")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("BROWN PLACE", body.upper())
        self.assertNotIn('id="match-label"', body)
        self.assertIn("couldn't find that building", html.unescape(body))
        self.assertIn("Did you mean", body)
        self.assertIn('<a href="/building?address=231+Echo+Place,+Bronx&amp;apt=4B">231 Echo Place, Bronx</a>', body.replace("%2C", ","))
        self.assertIn('<a class="minor" href="/area?street=Echo+Place&amp;borough=Bronx">All buildings on Echo Place</a>', body)

    def test_old_shared_links_on_the_front_page_are_covered_too(self):
        self.city()
        response = self.client().get("/?address=231+Ecco+Place%2C+Bronx")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("BROWN PLACE", response.get_data(as_text=True).upper())

    def test_a_different_number_shows_the_notice_and_the_street_link(self):
        response, body = self.get("address=1+Echo+Place%2C+Bronx")
        self.assertEqual(response.status_code, 200)
        text = html.unescape(body)
        self.assertIn("We couldn't find 1 Echo Place", text)
        self.assertRegex(text, r"Showing \d+ Echo Place\.")
        self.assertIn('<a href="/area?street=Echo+Place&amp;borough=Bronx">See all buildings on Echo Place</a>', body)
        self.assertLess(body.index("We couldn"), body.index('id="match-label"'))
        self.assertNotIn("Did you mean", body)

    def test_an_exact_match_shows_no_notice(self):
        response, body = self.get("address=231+Echo+Place%2C+Bronx")
        self.assertEqual(response.status_code, 200)
        self.assertIn("231 ECHO PLACE, Bronx, NY, USA", body)
        self.assertNotIn("We couldn", body)
        self.assertNotIn("Did you mean", body)

    def test_no_such_number_offers_the_street(self):
        response, body = self.get("address=999+Ecco+Place%2C+Bronx")
        self.assertEqual(response.status_code, 404)
        self.assertIn('<a href="/area?street=Echo+Place&amp;borough=Bronx">Buildings on Echo Place, Bronx</a>', body)
        self.assertNotIn("/building?address=999", body)

    def test_nonsense_and_a_failed_street_list_are_plain_not_found(self):
        response, body = self.get("address=231+Asdfgh+Street%2C+Bronx")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("Did you mean", body)
        for module in (bs, area_service, ss):
            module.clear_caches()
        response, body = self.get("address=231+Ecco+Place%2C+Bronx", fail_street_list=True)
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("Did you mean", body)
        self.assertNotIn("BROWN PLACE", body.upper())

    def test_a_hostile_typed_address_is_escaped_in_the_notice(self):
        hostile = '1 <b>x</b> Echo Place, Bronx'
        self.city(extra_geo={hostile: [feature("179", "ECHO PLACE", "Bronx", "2028080021")]})
        body = self.client().get("/building", query_string={"address": hostile}).get_data(as_text=True)
        self.assertNotIn("<b>x</b>", body)


if __name__ == "__main__":
    unittest.main()


class ReadingAWholeAddressInTheStreetBoxTests(unittest.TestCase):
    """street_suggestions.read_street_search (owner, 2026-10-14): tenants type
    their whole address into the front page's street box."""

    def test_the_street_borough_zip_and_number_are_separated(self):
        cases = {
            "231 Echo Place, Bronx, NY 10457": ("ECHO PLACE", "Bronx", "10457", "231"),
            "231 Echo Place": ("ECHO PLACE", None, None, "231"),
            "Echo Place": ("ECHO PLACE", None, None, None),
            "231 Echo Pl Apt 3B, Bronx": ("ECHO PL", "Bronx", None, "231"),
            "231 Echo Place #3B Bronx NY": ("ECHO PLACE", "Bronx", None, "231"),
            "231 Echo Place, Apt 3B, Bronx, NY 10457": ("ECHO PLACE", "Bronx", "10457", "231"),
            "1000 Grand Concourse Apt 5C Bronx NY 10451": ("GRAND CONCOURSE", "Bronx", "10451", "1000"),
            "37-02 82nd Street, Jackson Heights, NY 11372": ("82ND STREET", "Queens", "11372", "37-02"),
            "37-02 82nd St Jackson Heights NY 11372": ("82ND ST", "Queens", "11372", "37-02"),
            "30 Bay St Staten Island NY 10301": ("BAY ST", "Staten Island", "10301", "30"),
            "550 W 174th St, New York, NY 10033": ("W 174TH ST", "Manhattan", "10033", "550"),
            "Grand Concourse, The Bronx": ("GRAND CONCOURSE", "Bronx", None, None),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                got = ss.read_street_search(text)
                self.assertEqual((got.street, got.borough, got.zip_code, got.number), expected)

    def test_street_names_that_look_like_places_are_kept(self):
        for text, street in (("100 Queens Blvd", "QUEENS BLVD"), ("5 Bronx Park East", "BRONX PARK EAST"),
                             ("Park Avenue South", "PARK AVENUE SOUTH"), ("Avenue of the Americas", "AVENUE OF THE AMERICAS"),
                             ("St Nicholas Ave", "ST NICHOLAS AVE"), ("Broadway", "BROADWAY")):
            with self.subTest(text=text):
                got = ss.read_street_search(text)
                self.assertEqual((got.street, got.borough), (street, None))


class StreetBoxAddressRouteTests(_Case):
    def get(self, query):
        self.city()
        response = self.client().get("/area?" + query)
        return response, html.unescape(response.get_data(as_text=True))

    def test_a_whole_address_with_no_borough_chosen_finds_the_street(self):
        # Live 2026-10-14: 400 "Choose a borough."
        response, body = self.get("street=231+Echo+Place%2C+Bronx%2C+NY+10457")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Echo Place, Bronx", body)
        self.assertIn('<option value="Bronx" selected', body)

    def test_a_whole_address_with_the_borough_chosen_finds_the_street(self):
        # Live 2026-10-14: 404 "We couldn't find that street".
        response, body = self.get("street=231+Echo+Place%2C+Bronx%2C+NY+10457&borough=Bronx")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("couldn't find", body)

    def test_an_apartment_in_the_street_box_is_ignored(self):
        for street in ("231+Echo+Place+Apt+3B", "231+Echo+Place+%233B", "231+Echo+Place%2C+Apt+3B"):
            with self.subTest(street=street):
                response, _ = self.get(f"street={street}&borough=Bronx")
                self.assertEqual(response.status_code, 200)

    def test_a_house_number_links_to_that_buildings_own_record(self):
        response, body = self.get("street=231+Echo+Place+Apt+3B%2C+Bronx")
        self.assertIn('Your building: <a href="/building?address=231+Echo+Place,+Bronx">231 Echo Place, Bronx</a>', body)
        response, body = self.get("street=Echo+Place&borough=Bronx")
        self.assertNotIn("Your building", body)

    def test_a_typed_zip_narrows_but_never_blocks(self):
        response, body = self.get("street=231+Echo+Place%2C+Bronx%2C+NY+10457")
        self.assertIn("ZIP 10457", body)
        # No lots on the street in this ZIP: the whole street, not an error.
        response, body = self.get("street=231+Echo+Place%2C+Bronx%2C+NY+10451")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("ZIP 10451", body)
        # The ZIP box is the tenant's own choice and is still checked.
        response, body = self.get("street=Echo+Place&borough=Bronx&zip=10451")
        self.assertEqual(response.status_code, 400)

    def test_the_menu_wins_over_a_borough_in_the_text(self):
        response, body = self.get("street=Echo+Place%2C+Brooklyn&borough=Bronx")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Echo Place, Bronx", body)

    def test_a_typo_in_a_whole_address_still_gets_did_you_mean(self):
        response, body = self.get("street=231+Ecco+Place%2C+Bronx%2C+NY+10457")
        self.assertEqual(response.status_code, 404)
        self.assertIn("Echo Place, Bronx", body)
        self.assertIn("Did you mean", body)


class StreetQueryTimeoutTests(unittest.TestCase):
    def test_city_open_data_queries_get_the_longer_limit_and_other_services_do_not(self):
        # 2026-10-15 on Render: /building for 231 Echo Place was a 503 after
        # 12.9 s (a per-building count passed 6 s), then 200 on retry.
        for url, params in ((area_service.PLUTO_URL, {"$where": "borough='BX' AND address like '% ECHO PLACE'"}),
                            (bs.VIOLATIONS_URL, {"$where": "boroid='2' AND streetname='ECHO PLACE'"}),
                            (bs.VIOLATIONS_URL, {"$where": bs._building_where("2028100045")}),
                            (bs.VIOLATIONS_METADATA_URL, None)):
            with self.subTest(url=url):
                self.assertEqual(bs._timeout_for(url, params), bs.SLOW_QUERY_TIMEOUT_SECONDS)
        self.assertEqual(bs._timeout_for(bs.GEOSEARCH_URL, {"text": "231 Echo Place"}), bs.REQUEST_TIMEOUT_SECONDS)
