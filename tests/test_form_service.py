"""Tests for RA-81 form filling.

This form is filed with a state housing agency, so what these tests guard
is that every value lands in the RIGHT box, nothing is guessed, and
nothing is cut off. Earlier bugs that made it to main: a field name that
didn't exist ("Text1"), a writer that dropped the /AcroForm so nothing was
written at all, and a complaint line that took 120 characters when the box
holds about 45 -- the rest was clipped out of sight on the printed form.
"""

import json
import os
import re
import unittest
from unittest import mock

from pypdf import PdfReader

import form_service
from form_service import (
    COMPLAINT_FIELD,
    COMPLAINT_OVERFLOW_FIELD,
    CONTINUED,
    TEMPLATE_PATH,
    TENANT_APT_FIELD,
    TENANT_CITY_STATE_ZIP_FIELD,
    TENANT_NAME_FIELD,
    TENANT_STREET_FIELD,
    FormService,
    split_address,
)

FULL = {
    "status": "complete",
    "form": "RA-81",
    "tenant": {"name": "Maria Rodriguez", "street": "350 Grand Concourse", "apt": "4B",
               "city_state_zip": "Bronx, NY 10451", "phone_day": "(718) 555-0101", "phone_home": "(718) 555-0199"},
    "owner": {"name": "Concourse Realty LLC", "street": "100 Park Ave, Suite 9",
              "city_state_zip": "New York, NY 10017", "phone": "(212) 555-0142"},
    "subject_building": "",
    "regulation": "rent_stabilized",
    "coop_condo": {"unit_owner": "", "corporation": "", "managing_agent": ""},
    "seven_a_administrator": False,
    "move_in_date": "2019-06-01",
    "apartments_in_building": "48",
    "scrie_drie": "no",
    "section8": "housing_choice_voucher",
    "voucher_number": "HCV-12345",
    "notice": {"date": "2026-09-02", "method": "certified_mail"},
    "conditions": {"kitchen": "Sink leaks under the cabinet since July.", "bathroom": "", "bedroom": "",
                   "living_room": "", "dining_room": "", "hall": "", "other": ""},
}

LEGACY = {"status": "complete", "name": "Maria Rodriguez",
          "address": "350 Grand Concourse Apt 4B, Bronx, NY 10451", "complaint": "No heat since November 3rd."}


def _with(**changes):
    data = json.loads(json.dumps(FULL))
    for path, value in changes.items():
        target = data
        keys = path.split("__")
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value
    return data


def _fill(data):
    path = FormService.fill_tenant_form(data)
    reader = PdfReader(path)
    fields = reader.get_fields() or {}
    values = {name: str(f["/V"]) for name, f in fields.items() if f.get("/V") not in (None, "/Off")}
    return path, reader, values


def _rects():
    rects = {}
    for number, page in enumerate(PdfReader(TEMPLATE_PATH).pages, 1):
        for ref in page["/Annots"]:
            widget = ref.get_object()
            rects[str(widget["/T"])] = (number, [round(float(v)) for v in widget["/Rect"]])
    return rects


class FieldMapTests(unittest.TestCase):
    """The names say nothing, so the positions are what's checked."""

    @classmethod
    def setUpClass(cls):
        cls.rects = _rects()

    def test_every_field_this_code_writes_exists(self):
        names = {
            form_service.TENANT_NAME_FIELD, form_service.TENANT_STREET_FIELD, form_service.TENANT_APT_FIELD,
            form_service.TENANT_CITY_STATE_ZIP_FIELD, form_service.TENANT_PHONE_DAY_FIELD,
            form_service.TENANT_PHONE_HOME_FIELD, form_service.OWNER_NAME_FIELD, form_service.OWNER_STREET_FIELD,
            form_service.OWNER_CITY_STATE_ZIP_FIELD, form_service.OWNER_PHONE_FIELD,
            form_service.SUBJECT_BUILDING_FIELD, form_service.COOP_CHECKBOX, form_service.COOP_UNIT_OWNER_FIELD,
            form_service.COOP_CORPORATION_FIELD, form_service.COOP_MANAGING_AGENT_FIELD,
            form_service.SEVEN_A_CHECKBOX, form_service.APARTMENTS_FIELD, form_service.VOUCHER_NUMBER_FIELD,
            *form_service.MOVE_IN_DATE_FIELDS, *form_service.NOTICE_DATE_FIELDS,
            *form_service.REGULATION_CHECKBOXES.values(), *form_service.SCRIE_CHECKBOXES.values(),
            *form_service.SECTION8_CHECKBOXES.values(), *form_service.NOTICE_METHOD_CHECKBOXES.values(),
        }
        for room in form_service.ROOMS:
            names.update(room[2:5])
        for name in names:
            with self.subTest(field=name):
                self.assertIn(name, self.rects)

    def test_tenant_fields_are_in_the_left_column_and_owner_fields_in_the_right(self):
        for name in ("Name", "Text2", "Text3", "State Zip Code", "Text4", "Text6"):
            self.assertLess(self.rects[name][1][2], 320, name)
        for name in ("Name_2", "NumberStreet", "State Zip Code_2", "Text5"):
            self.assertGreater(self.rects[name][1][0], 340, name)

    def test_checkbox_rows_are_in_the_printed_order(self):
        def xs(names):
            return [self.rects[n][1][0] for n in names]
        regulation = list(form_service.REGULATION_CHECKBOXES.values())
        self.assertEqual(xs(regulation), sorted(xs(regulation)))  # stabilized, controlled, hotel, SRO
        notice = list(form_service.NOTICE_METHOD_CHECKBOXES.values())
        self.assertEqual(xs(notice), sorted(xs(notice)))  # regular, certified, personal
        first_row = [form_service.SECTION8_CHECKBOXES[k] for k in ("none", "hud", "nycha")]
        self.assertEqual(xs(first_row), sorted(xs(first_row)))
        self.assertLess(self.rects["Check Box17"][1][0], self.rects["Check Box18"][1][0])  # SCRIE yes, no

    def test_each_room_checkbox_sits_beside_its_own_line_and_the_rooms_run_down_the_page(self):
        tops = []
        for key, label, checkbox, first, second, *_ in form_service.ROOMS:
            box_y = self.rects[checkbox][1][1]
            line_y = self.rects[first][1][1]
            with self.subTest(room=key):
                self.assertLess(abs(box_y - line_y), 20)
                self.assertLess(self.rects[second][1][1], line_y)  # continuation is below
            tops.append(line_y)
        self.assertEqual(tops, sorted(tops, reverse=True))

    def test_room_widths_match_the_template(self):
        for key, label, checkbox, first, second, first_width, second_width in form_service.ROOMS:
            with self.subTest(room=key):
                x1, _, x2, _ = self.rects[first][1]
                self.assertAlmostEqual(x2 - x1, first_width, delta=2)
                x1, _, x2, _ = self.rects[second][1]
                self.assertAlmostEqual(x2 - x1, second_width, delta=2)


class FullFormTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path, cls.reader, cls.values = _fill(FULL)

    def test_tenant_and_owner_details(self):
        v = self.values
        self.assertEqual((v["Name"], v["Text2"], v["Text3"], v["State Zip Code"]),
                         ("Maria Rodriguez", "350 Grand Concourse", "4B", "Bronx, NY 10451"))
        self.assertEqual((v["Text4"], v["Text6"]), ("(718) 555-0101", "(718) 555-0199"))
        self.assertEqual((v["Name_2"], v["NumberStreet"], v["State Zip Code_2"], v["Text5"]),
                         ("Concourse Realty LLC", "100 Park Ave, Suite 9", "New York, NY 10017", "(212) 555-0142"))

    def test_part_one(self):
        v = self.values
        self.assertEqual(v["Check Box11"], "/Yes")  # rent stabilized
        for other in ("Check Box13", "Check Box14", "Check Box15", "Check Box12", "Check Box16"):
            self.assertNotIn(other, v)
        self.assertEqual([v[n] for n in form_service.MOVE_IN_DATE_FIELDS], ["06", "01", "2019"])
        self.assertEqual(v[form_service.APARTMENTS_FIELD], "48")
        self.assertEqual(v["Check Box18"], "/Yes")  # SCRIE: no
        self.assertNotIn("Check Box17", v)
        self.assertEqual(v["Check Box22"], "/Yes")  # Housing Choice Voucher
        self.assertEqual(v[form_service.VOUCHER_NUMBER_FIELD], "HCV-12345")

    def test_part_two(self):
        v = self.values
        self.assertEqual([v[n] for n in form_service.NOTICE_DATE_FIELDS], ["09", "02", "2026"])
        self.assertEqual(v["Check Box25"], "/Yes")  # certified mail
        self.assertEqual(v["Check Box10"], "/Yes")  # kitchen
        self.assertEqual(v["Kitchen"], "Sink leaks under the cabinet since July.")
        for box in ("Check Box27", "Check Box28", "Check Box29", "Check Box30", "Check Box31", "Check Box32"):
            self.assertNotIn(box, v)

    def test_the_signature_date_is_left_for_the_tenant(self):
        self.assertNotIn("Date", self.values)

    def test_every_filled_text_field_has_its_own_appearance_and_viewers_dont_redraw(self):
        need = self.reader.trailer["/Root"]["/AcroForm"].get("/NeedAppearances")
        self.assertIn(getattr(need, "value", need), (False, None))
        for page in self.reader.pages:
            for ref in page["/Annots"]:
                widget = ref.get_object()
                if widget.get("/FT") == "/Tx" and widget.get("/V"):
                    with self.subTest(field=str(widget["/T"])):
                        stream = widget["/AP"]["/N"].get_object().get_data().decode("cp1252")
                        self.assertIn(str(widget["/V"]).replace("(", "\\(").replace(")", "\\)"), stream)

    def test_two_tenants_never_share_a_file(self):
        other, _, values = _fill(_with(tenant__name="Tenant Two"))
        self.assertNotEqual(self.path, other)
        self.assertEqual(values["Name"], "Tenant Two")
        self.assertEqual(self.values["Name"], "Maria Rodriguez")


class AccuracyTests(unittest.TestCase):
    def test_nothing_is_guessed(self):
        data = _with(regulation="maybe", scrie_drie="", section8="lots", move_in_date="last spring",
                     notice__method="pigeon", notice__date="")
        _, _, v = _fill(data)
        for box in ("Check Box11", "Check Box13", "Check Box14", "Check Box15", "Check Box17", "Check Box18",
                    "Check Box19", "Check Box20", "Check Box21", "Check Box22", "Check Box23",
                    "Check Box24", "Check Box25", "Check Box26"):
            self.assertNotIn(box, v)
        for name in form_service.MOVE_IN_DATE_FIELDS + form_service.NOTICE_DATE_FIELDS:
            self.assertNotIn(name, v)

    def test_a_copied_placeholder_is_not_a_choice(self):
        # The prompt shows the options as "yes | no | (empty if unknown)".
        intake = form_service.normalize_intake(_with(scrie_drie="yes | no | (empty if unknown)",
                                                     regulation="rent_stabilized | rent_controlled"))
        self.assertEqual((intake["scrie_drie"], intake["regulation"]), ("", ""))

    def test_the_tenant_is_never_written_into_the_owner_column(self):
        _, _, v = _fill(_with(owner={"name": "", "street": "", "city_state_zip": "", "phone": ""}))
        for name in ("Name_2", "NumberStreet", "State Zip Code_2", "Text5"):
            self.assertNotIn(name, v)

    def test_long_text_is_shrunk_to_fit_its_box_not_clipped(self):
        _, reader, _ = _fill(_with(tenant__street="1234 Grand Concourse Boulevard East"))
        for ref in reader.pages[0]["/Annots"]:
            widget = ref.get_object()
            if widget["/T"] == "Text2":
                size = float(re.search(r"([\d.]+) Tf", str(widget["/DA"])).group(1))
                x1, _, x2, _ = (float(n) for n in widget["/Rect"])
                self.assertLessEqual(form_service.text_width(str(widget["/V"]), size), x2 - x1 - 4)
                self.assertLess(size, 10)

    def test_a_month_only_date_fills_month_and_year(self):
        _, _, v = _fill(_with(move_in_date="2019-06"))
        self.assertEqual(v["2 I moved into my apartment on"], "06")
        self.assertNotIn("undefined", v)
        self.assertEqual(v["undefined_2"], "2019")

    def test_impossible_dates_are_left_blank(self):
        self.assertEqual(form_service.parse_date("2026-02-30"), ("", "", ""))
        self.assertEqual(form_service.parse_date("09/02/2026"), ("09", "02", "2026"))

    def test_coop_and_7a_and_subject_building(self):
        _, _, v = _fill(_with(coop_condo={"unit_owner": "Pat Lee", "corporation": "Echo Owners Corp",
                                          "managing_agent": "Acme Mgmt"},
                              seven_a_administrator=True, subject_building="231 Echo Place, Bronx, NY 10457"))
        self.assertEqual(v["Check Box12"], "/Yes")
        self.assertEqual(v["Check Box16"], "/Yes")
        self.assertEqual(v["Unit OwnerProprietary Lessee"], "Pat Lee")
        self.assertEqual(v["Text8"], "231 Echo Place, Bronx, NY 10457")


class NothingIsCutOffTests(unittest.TestCase):
    LONG = ("Rear bedroom: the window does not close and lets in cold air and rain. The radiator in the same "
            "room has been cold since October 1 and the landlord was told several times by phone and by letter "
            "but nothing has been done, (and) the ceiling above the bed has a crack that is getting wider.")

    def test_wrapped_lines_fit_their_widths(self):
        for key, label, checkbox, first, second, w1, w2 in form_service.ROOMS:
            (line1, line2), _ = form_service.wrap("word " * 30, [w1 - 4, w2 - 4])
            with self.subTest(room=key):
                self.assertLessEqual(form_service.text_width(line1, 11), w1 - 4)
                self.assertLessEqual(form_service.text_width(line2, 11), w2 - 4)

    def test_overflow_is_marked_and_printed_in_full_on_an_attached_page(self):
        _, reader, v = _fill(_with(conditions__bedroom=self.LONG))
        self.assertTrue(v["undefined_9"].endswith(CONTINUED.strip()))
        self.assertEqual(len(reader.pages), 3)
        flat = " ".join(reader.pages[2].extract_text().split())
        self.assertIn("Attachment to Form RA-81", flat)
        self.assertIn("Maria Rodriguez", flat)
        self.assertIn(" ".join(self.LONG.split()), flat)

    def test_no_attachment_when_everything_fits(self):
        _, reader, _ = _fill(FULL)
        self.assertEqual(len(reader.pages), 2)

    def test_what_fits_on_the_form_is_the_start_of_the_description(self):
        _, _, v = _fill(_with(conditions__bedroom=self.LONG))
        on_form = v["Bedroom Specify which bedroom if more than one"] + " " + v["undefined_9"].replace(CONTINUED.strip(), "")
        self.assertTrue(" ".join(self.LONG.split()).startswith(" ".join(on_form.split())))


class LegacyIntakeTests(unittest.TestCase):
    """The first version's {name, address, complaint} still fills correctly."""

    def test_legacy_fields_land_in_the_tenant_boxes_and_other_room(self):
        _, _, v = _fill(LEGACY)
        self.assertEqual(v[TENANT_NAME_FIELD], "Maria Rodriguez")
        self.assertEqual(v[TENANT_STREET_FIELD], "350 Grand Concourse")
        self.assertEqual(v[TENANT_APT_FIELD], "4B")
        self.assertEqual(v[TENANT_CITY_STATE_ZIP_FIELD], "Bronx, NY 10451")
        self.assertIn("No heat", v[COMPLAINT_FIELD])
        self.assertEqual(v["Check Box32"], "/Yes")

    def test_empty_intake_writes_nothing(self):
        _, _, v = _fill({"status": "complete", "name": "", "address": "", "complaint": ""})
        self.assertEqual(v, {})

    def test_a_long_legacy_complaint_continues_on_the_attachment(self):
        _, reader, v = _fill(dict(LEGACY, complaint="Detail. " * 120))
        self.assertTrue((v[COMPLAINT_FIELD] + v[COMPLAINT_OVERFLOW_FIELD]).endswith(CONTINUED.strip()))
        self.assertEqual(len(reader.pages), 3)


class MissingFieldsTests(unittest.TestCase):
    def test_complete_intake_is_ready(self):
        self.assertEqual(form_service.missing_fields(form_service.normalize_intake(FULL)), [])

    def test_each_required_part_is_named(self):
        missing = " ".join(form_service.missing_fields(form_service.normalize_intake({"status": "complete"})))
        for phrase in ("your full name", "your full mailing address", "landlord's or managing agent's name",
                       "landlord's mailing address", "rent stabilized", "which room"):
            self.assertIn(phrase, missing)

    def test_legacy_intake_now_asks_for_the_landlord_and_status(self):
        self.assertEqual(len(form_service.missing_fields(form_service.normalize_intake(LEGACY))), 3)


class ParseIntakeTests(unittest.TestCase):
    def test_raw_fenced_and_prefixed_json(self):
        raw = json.dumps(FULL)
        for reply in (raw, f"```json\n{raw}\n```", f"Here is your form:\n{raw}"):
            with self.subTest(reply=reply[:20]):
                self.assertEqual(form_service.parse_intake(reply)["tenant"]["name"], "Maria Rodriguez")

    def test_not_an_intake(self):
        for reply in ("Plain answer.", '{"status": "in_progress"}', '{"status": "complete"', "[1, 2]", ""):
            with self.subTest(reply=reply):
                self.assertIsNone(form_service.parse_intake(reply))


class AddressSplittingTests(unittest.TestCase):
    def test_splits_street_apartment_and_city_state_zip(self):
        self.assertEqual(split_address("350 Grand Concourse Apt 4B, Bronx, NY 10451"),
                         ("350 Grand Concourse", "4B", "Bronx, NY 10451"))

    def test_handles_an_address_with_no_apartment(self):
        self.assertEqual(split_address("55 Fake St, Queens, NY 11101"), ("55 Fake St", "", "Queens, NY 11101"))

    def test_anything_unparseable_stays_on_the_street_line(self):
        self.assertEqual(split_address("somewhere vague"), ("somewhere vague", "", ""))

    def test_empty_address(self):
        self.assertEqual(split_address(""), ("", "", ""))
        self.assertEqual(split_address(None), ("", "", ""))


class HelveticaWidthTests(unittest.TestCase):
    def test_table_matches_the_standard_metrics(self):
        try:
            from reportlab.pdfbase.pdfmetrics import getFont
        except ImportError:
            self.skipTest("reportlab not installed; the table was checked against it when written")
        widths = getFont("Helvetica").widths
        self.assertEqual(form_service._HELVETICA, [widths[c] for c in range(32, 127)])


class BytesTests(unittest.TestCase):
    def test_fill_to_bytes_leaves_no_temp_file(self):
        created = []
        real = FormService.fill_tenant_form

        def spy(data):
            created.append(real(data))
            return created[-1]

        with mock.patch.object(FormService, "fill_tenant_form", side_effect=spy):
            data = form_service.fill_to_bytes(FULL)
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertFalse(os.path.exists(created[0]))


if __name__ == "__main__":
    unittest.main()
