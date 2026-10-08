"""Street + borough lookup (area_service.py, /area, the front page).

The fixture is real data captured 2026-10-07 for Echo Place in the Bronx:
GeoSearch, MapPLUTO lots, open HPD violations by street, community board
directory rows and the State district maps. Echo Place is a good test
street because it is short but still crosses two Council districts
(14, 15) and two State Senate districts (32, 33).
"""

import json
import os
import unittest
from types import SimpleNamespace
from unittest import mock

import flask

import area_service
import building_service
from routes import main_bp
from tests.app_test_support import configure_test_app

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TEMPLATES = os.path.join(_ROOT, "templates")
with open(os.path.join(_ROOT, "tests", "fixtures", "area", "echo_place.json"), encoding="utf-8") as _h:
    FX = json.load(_h)


def fake_get_json(fail=()):
    calls = []

    def _get(url, params=None):
        params = params or {}
        calls.append((url, params))
        for kind in fail:
            if kind in url:
                raise building_service.LookupUnavailable("down")
        if url == building_service.GEOSEARCH_URL:
            text = params["text"].upper()
            if "ECHO" in text:
                return FX["geo_echo"]
            if "CONCOURSE" in text:
                return FX["geo_grand_concourse"]
            if "BROADWAY" in text:
                return FX["geo_broadway"]
            return FX["geo_nonsense"]
        if url == area_service.PLUTO_URL:
            where = params["$where"]
            if "ECHO PLACE" in where:
                return FX["lots_echo"]
            if "GRAND CONCOURSE" in where:
                return FX["lots_grand_concourse_near_echo"] + FX["lots_grand_concourse_far"]
            return []
        if url == building_service.VIOLATIONS_URL:
            return FX["hpd_echo"]
        if url == area_service.COMMUNITY_BOARDS_URL:
            return FX["community_boards"]
        for name, (layer_url, _field) in area_service.DISTRICT_LAYERS.items():
            if url == layer_url:
                return FX["arcgis"][name]
        raise AssertionError(f"unexpected URL {url}")

    return _get, calls


class FakeOfficialsClient:
    def __init__(self, rows=None, fail=False):
        self.rows = rows if rows is not None else [
            {"office": "council", "district": 15, "name": "Oswald J. Feliz", "refreshed_at": "2026-10-07T12:00:00+00:00"},
            {"office": "council", "district": 14, "name": "Council Fourteen", "refreshed_at": "2026-10-07T12:00:00+00:00"},
            {"office": "assembly", "district": 86, "name": "Yudelka Tapia", "refreshed_at": "2026-10-07T12:00:00+00:00"},
            {"office": "state_senate", "district": 32, "name": "Luis R. Sepúlveda", "refreshed_at": "2026-10-07T12:00:00+00:00"},
        ]
        self.fail = fail

    def table(self, name):
        assert name == "elected_officials"
        client = self

        class Q:
            def select(self, *_):
                return self

            def limit(self, *_):
                return self

            def execute(self):
                if client.fail:
                    raise RuntimeError("db down")
                return SimpleNamespace(data=client.rows)

        return Q()


class _Isolated(unittest.TestCase):
    def setUp(self):
        area_service.clear_caches()
        self.addCleanup(area_service.clear_caches)

    def lookup(self, *args, fail=(), client=None, **kwargs):
        getter, calls = fake_get_json(fail)
        with mock.patch.object(area_service, "_get_json", getter):
            report = area_service.lookup(*args, client=client or FakeOfficialsClient(), **kwargs)
        return report, calls


class StreetWordsTests(unittest.TestCase):
    def test_typed_forms_become_the_city_spelling(self):
        cases = {
            "231 E. 149th St": ["EAST", "149", "STREET"],
            "echo pl": ["ECHO", "PLACE"],
            "St Nicholas Ave": ["SAINT", "NICHOLAS", "AVENUE"],
            "Fifth Avenue": ["5", "AVENUE"],
            "Avenue E": ["AVENUE", "E"],
            "102-15 Queens Blvd": ["QUEENS", "BOULEVARD"],
        }
        for typed, words in cases.items():
            with self.subTest(typed=typed):
                self.assertEqual(area_service.street_words(typed), words)

    def test_a_fuzzy_match_is_never_accepted(self):
        typed = area_service.street_words
        self.assertTrue(area_service._matches(typed("e 149th st"), "EAST 149 STREET"))
        self.assertFalse(area_service._matches(typed("e 149th st"), "WEST 149 STREET"))
        self.assertFalse(area_service._matches(typed("e 149th st"), "EAST 149 AVENUE"))
        self.assertFalse(area_service._matches(typed("asdfgh street"), "211 STREET"))
        self.assertFalse(area_service._matches(typed("broadway"), "B'WAY"))

    def test_bad_input(self):
        for bad in ("", "   ", "x" * 81):
            with self.subTest(bad=bad), self.assertRaises(area_service.InvalidArea):
                area_service.clean_street(bad)
        with self.assertRaises(area_service.InvalidArea):
            area_service.borough_from("Narnia")
        for z in ("123", "abcde", "90210"):
            with self.subTest(z=z), self.assertRaises(area_service.InvalidArea):
                area_service.clean_zip(z)
        self.assertEqual(area_service.clean_zip(" 10457 "), "10457")
        self.assertEqual(area_service.borough_from("bx"), "Bronx")


class LookupTests(_Isolated):
    def test_echo_place(self):
        report, calls = self.lookup("Echo Pl", "Bronx")
        self.assertEqual(report.street, "ECHO PLACE")
        self.assertEqual(report.label, "Echo Place, Bronx")
        self.assertEqual(sorted(report.council), [14, 15])
        self.assertEqual(report.community_districts, ["205"])
        self.assertEqual(report.assembly, [86])
        self.assertEqual(report.state_senate, [32, 33])
        self.assertEqual(report.congress, [15])
        self.assertTrue(report.spans_districts)
        self.assertEqual(sorted(report.zip_codes), ["10453", "10457"])
        self.assertEqual(report.precincts, ["46"])
        self.assertIn("apartment buildings", report.summary)
        self.assertGreater(report.homes, 400)
        self.assertEqual(report.zoning, "residential")
        self.assertEqual(report.community_boards[0]["name"], "Bronx Community Board 5")
        # The PLUTO query is scoped to the borough and the city's spelling.
        pluto = [p for u, p in calls if u == area_service.PLUTO_URL][0]
        self.assertIn("borough='BX'", pluto["$where"])
        self.assertIn("'% ECHO PLACE'", pluto["$where"])

    def test_officials_get_names_and_official_links(self):
        report, _ = self.lookup("Echo Place", "Bronx")
        by = {(o.office, o.district): o for o in report.officials}
        self.assertEqual(by[("council", 15)].name, "Oswald J. Feliz")
        self.assertEqual(by[("council", 15)].url, "https://council.nyc.gov/district-15/")
        self.assertEqual(by[("assembly", 86)].url, "https://nyassembly.gov/mem/?ad=086")
        self.assertEqual(by[("state_senate", 32)].url, "https://www.nysenate.gov/district/32")
        # Not in the table (vacancy, or not loaded yet): number and link, no name.
        self.assertIsNone(by[("state_senate", 33)].name)
        self.assertEqual(by[("congress", 15)].url, area_service.HOUSE_FIND_REP_URL)
        self.assertEqual(report.officials_as_of, "2026-10-07")

    def test_buildings_carry_their_open_violations(self):
        report, _ = self.lookup("Echo Place", "Bronx")
        self.assertTrue(report.violations_available)
        by_bbl = {b.bbl: b for b in report.buildings}
        hpd = {}
        for row in FX["hpd_echo"]:
            if row.get("bbl"):
                hpd[row["bbl"]] = hpd.get(row["bbl"], 0) + int(row["n"])
        some = next(b for b in hpd if b in by_bbl)
        self.assertEqual(by_bbl[some].open_total, hpd[some])
        numbers = [int(b.address.split()[0].split("-")[0]) for b in report.buildings]
        self.assertEqual(numbers, sorted(numbers))
        self.assertTrue(all(b.address.endswith("Echo Place") for b in report.buildings))

    def test_corner_buildings_hpd_files_on_this_street_are_listed(self):
        # 175 Echo Place: its tax lot is addressed on another street, but
        # HPD files its 40+ open violations under Echo Place.
        report, _ = self.lookup("Echo Place", "Bronx")
        corner = next(b for b in report.buildings if b.bbl == "2028080021")
        self.assertEqual(corner.address, "175 Echo Place")
        self.assertEqual(corner.open_counts.get("C"), 5)
        self.assertEqual(report.officials_as_of_label, "October 7, 2026")
        only_10457, _ = self.lookup("Echo Place", "Bronx", zip_code="10457")
        self.assertNotIn("2028080021", {b.bbl for b in only_10457.buildings})  # it is in 10453

    def test_zip_narrows_the_street(self):
        report, _ = self.lookup("Echo Place", "Bronx", zip_code="10457")
        self.assertEqual(report.zip_codes, ["10457"])
        self.assertEqual(report.council, [15])
        with self.assertRaises(area_service.InvalidArea):
            self.lookup("Echo Place", "Bronx", zip_code="10001")

    def test_cross_street_keeps_only_lots_near_the_corner(self):
        whole, _ = self.lookup("Echo Place", "Bronx")
        near, _ = self.lookup("Echo Place", "Bronx", cross_street="Grand Concourse")
        self.assertEqual(near.cross_street, "GRAND CONCOURSE")
        self.assertLessEqual(near.lot_count, whole.lot_count)
        self.assertGreater(near.lot_count, 0)

    def test_streets_that_do_not_meet(self):
        getter, _ = fake_get_json()

        def far_only(url, params=None):
            if url == area_service.PLUTO_URL and "GRAND CONCOURSE" in params["$where"]:
                return FX["lots_grand_concourse_far"]
            return getter(url, params)

        with mock.patch.object(area_service, "_get_json", far_only), self.assertRaises(area_service.CrossStreetNotFound):
            area_service.lookup("Echo Place", "Bronx", cross_street="Grand Concourse", client=FakeOfficialsClient())

    def test_a_made_up_street_is_not_found_rather_than_guessed(self):
        with self.assertRaises(area_service.StreetNotFound):
            self.lookup("Asdfgh Street", "Queens")

    def test_broadway_is_not_b_way(self):
        getter, _ = fake_get_json()
        with mock.patch.object(area_service, "_get_json", getter):
            self.assertEqual(area_service.resolve_street("broadway", "Manhattan"), "BROADWAY")

    def test_optional_sources_failing_do_not_fail_the_page(self):
        report, _ = self.lookup("Echo Place", "Bronx", fail=("arcgis", "ruf7-3wgc", "wvxf-dwi5"),
                                client=FakeOfficialsClient(fail=True))
        self.assertEqual(report.assembly, [])
        self.assertEqual(report.community_boards, [])
        self.assertFalse(report.violations_available)
        self.assertTrue(report.buildings)
        council = [o for o in report.officials if o.office == "council"]
        self.assertTrue(council and all(o.name is None for o in council))

    def test_property_data_down_is_lookup_unavailable(self):
        with self.assertRaises(building_service.LookupUnavailable):
            self.lookup("Echo Place", "Bronx", fail=("64uk-42ks",))

    def test_street_names_are_escaped_in_queries(self):
        self.assertEqual(area_service._soql_text("O'BRIEN PLACE"), "O''BRIEN PLACE")


def _client(logged_in=False):
    app = flask.Flask(__name__, template_folder=_TEMPLATES)
    app.secret_key = "t"

    class FakeSupabase:
        client = FakeOfficialsClient()

        def build_user_scoped_client(self, token):
            return object()

    app.config["SUPABASE_SERVICE"] = FakeSupabase()
    app.config["AI_SERVICE"] = SimpleNamespace(is_ready=lambda: True)
    app.register_blueprint(main_bp)
    configure_test_app(app)
    client = app.test_client()
    if logged_in:
        with client.session_transaction() as s:
            s["user_id"] = "u1"
    return client


class RouteTests(_Isolated):
    def get(self, path):
        getter, _ = fake_get_json()
        with mock.patch.object(area_service, "_get_json", getter):
            return _client().get(path)

    def test_front_page_is_the_street_search(self):
        response = self.get("/")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('name="street"', body)
        self.assertIn('name="borough"', body)
        self.assertIn("No house number needed", body)
        self.assertIn('href="/building"', body)

    def test_old_address_links_still_go_to_the_building_lookup(self):
        with mock.patch.object(building_service, "lookup", side_effect=building_service.AddressNotFound("x")):
            response = _client().get("/?address=231+echo+pl")
        self.assertEqual(response.status_code, 404)
        self.assertIn("couldn", response.get_data(as_text=True))

    def test_results_page(self):
        response = self.get("/area?street=echo+pl&borough=Bronx")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Echo Place, Bronx", body)
        self.assertIn("This street crosses more than one district", body)
        self.assertIn("Oswald J. Feliz", body)
        self.assertIn("https://nyassembly.gov/mem/?ad=086", body)
        self.assertIn("Bronx Community Board 5", body)
        self.assertIn("/building?address=", body)
        self.assertIn("/resources?borough=Bronx&amp;cd=205", body)
        # Public pages never name where the data comes from in the backend.
        for word in ("PLUTO", "GeoSearch", "arcgis", "Socrata"):
            self.assertNotIn(word, body)

    def test_errors_are_plain_and_have_the_right_status(self):
        cases = {
            "/area?street=asdfgh+street&borough=Queens": (404, "find that street"),
            "/area?street=echo+pl&borough=": (400, "Choose a borough"),
            "/area?street=echo+pl&borough=Bronx&zip=10001": (400, "ZIP code 10001"),
        }
        for path, (status, text) in cases.items():
            with self.subTest(path=path):
                response = self.get(path)
                self.assertEqual(response.status_code, status)
                self.assertIn(text, response.get_data(as_text=True))

    def test_city_outage_is_a_503(self):
        getter, _ = fake_get_json(fail=("geosearch",))
        with mock.patch.object(area_service, "_get_json", getter):
            response = _client().get("/area?street=echo+pl&borough=Bronx")
        self.assertEqual(response.status_code, 503)

    def test_lookups_are_rate_limited_but_the_empty_page_is_not(self):
        from routes import _no_lookup_requested
        app = flask.Flask(__name__)
        with app.test_request_context("/"):
            self.assertTrue(_no_lookup_requested())
        with app.test_request_context("/area?street=echo"):
            self.assertFalse(_no_lookup_requested())


class ResourcesPageTests(_Isolated):
    def get(self, path):
        getter, _ = fake_get_json()
        with mock.patch.object(area_service, "_get_json", getter):
            return _client().get(path)

    def test_citywide_page(self):
        body = self.get("/resources").get_data(as_text=True)
        self.assertIn("Free help", body)
        self.assertIn("(212) 962-4795", body)
        self.assertIn("Report a problem", body)
        self.assertNotIn("Housing Court in", body)

    def test_borough_and_community_board(self):
        body = self.get("/resources?borough=Bronx&cd=205").get_data(as_text=True)
        self.assertIn("Housing Court in Bronx", body)
        self.assertIn("1118 Grand Concourse", body)
        self.assertIn("Bronx Community Board 5", body)
        self.assertIn('aria-current="page"', body)

    def test_a_community_board_from_another_borough_is_ignored(self):
        body = self.get("/resources?borough=Queens&cd=205").get_data(as_text=True)
        self.assertNotIn("Bronx Community Board 5", body)

    def test_the_old_wrong_helpline_is_gone(self):
        body = self.get("/resources").get_data(as_text=True)
        self.assertNotIn("466-3456", body)
        self.assertIn("1 (833) 499-0343", body)


if __name__ == "__main__":
    unittest.main()
