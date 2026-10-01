"""Fills the DHCR RA-81 form ("Application For A Rent Reduction Based Upon
Decreased Service(s) - Individual Apartment") from the intake JSON the
assistant produces.

FIELD NAMES ARE NOT GUESSABLE FROM THIS FORM -- READ BEFORE EDITING.
The RA-81's internal field names are mostly what Acrobat auto-generated,
so they are useless as documentation: the tenant's street address is
called "Text2", the apartment number "Text3", and there are two fields
called "Name" and "Name_2" for the tenant and the owner respectively.
Every mapping below was derived from each widget's rectangle, matched
against the rendered page, and then confirmed by filling a sample and
looking at it (tests/test_form_service.py pins the positions):

  page 1
    Name / Text2 / Text3 / State Zip Code      tenant name / street / apt / city-state-zip
    Text4 / Text6                              tenant telephone, business / residence
    Name_2 / NumberStreet / State Zip Code_2   owner name / street / city-state-zip
    Text5                                      owner telephone
    Text8                                      subject building, if different
    Check Box11 / 13 / 14 / 15                 rent stabilized / rent controlled / hotel stabilized / SRO
    Check Box12 + three text lines             co-op/condo (unit owner, corporation, managing agent)
    Check Box16                                managed by a 7A administrator
    "2 I moved into..." / undefined / undefined_2   move-in date: month / day / year
    "3 The total number of apartments..."      apartments in the building
    Check Box17 / 18                           SCRIE or DRIE: yes / no
    Check Box19 / 20 / 21 / 22 / 23            Section 8: none / HUD / NYCHA / Housing Choice Voucher / HPD
    "If applicable enter Certificate..."       voucher number

  page 2
    "4 The conditions..." / undefined_3 / undefined_4   date the owner was told in writing
    Check Box24 / 25 / 26                      letter sent by regular mail / certified mail / delivered by hand
    one checkbox + a line + a continuation line per room:
      Kitchen (Check Box10, Kitchen, undefined_5)
      Bathroom (Check Box27, Bathroom, undefined_7)
      Bedroom (Check Box28, "Bedroom Specify...", undefined_9)
      Living room (Check Box29, Living Room, undefined_11)
      Dining room (Check Box30, Dining Room, undefined_13)
      Hall (Check Box31, Hall Inside Apartment, undefined_15)
      Other (Check Box32, "Other Specify...", "Part III  Tenants Affirmation" -- named
             for the heading under it; it is the Other continuation line)
    Date                                       Part III date -- left for the tenant, who signs by hand

ACCURACY RULES
- Nothing is guessed. A value the tenant didn't give stays blank; the
  route asks for anything REQUIRED before a PDF is made (missing_fields).
- Nothing is cut off. Room descriptions are wrapped to the width each
  line really has; anything that doesn't fit is marked "(continued on
  attached page)" and printed in full on a page appended to the form.
- The owner's column only ever gets the owner's details.

If you change this mapping, regenerate a filled sample and LOOK at it.
This is a document a tenant files with a state agency; a plausible-looking
form with the name in the landlord's box is worse than an empty one.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date

from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    BooleanObject,
    DecodedStreamObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    TextStringObject,
)

# Resolved against this file, not the process working directory.
TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "ra-81-fillable.pdf")

# ---- page 1
TENANT_NAME_FIELD = "Name"
TENANT_STREET_FIELD = "Text2"
TENANT_APT_FIELD = "Text3"
TENANT_CITY_STATE_ZIP_FIELD = "State Zip Code"
TENANT_PHONE_DAY_FIELD = "Text4"
TENANT_PHONE_HOME_FIELD = "Text6"
OWNER_NAME_FIELD = "Name_2"
OWNER_STREET_FIELD = "NumberStreet"
OWNER_CITY_STATE_ZIP_FIELD = "State Zip Code_2"
OWNER_PHONE_FIELD = "Text5"
SUBJECT_BUILDING_FIELD = "Text8"
COOP_CHECKBOX = "Check Box12"
COOP_UNIT_OWNER_FIELD = "Unit OwnerProprietary Lessee"
COOP_CORPORATION_FIELD = "Name of Cooperative CorpCondo Assn"
COOP_MANAGING_AGENT_FIELD = "Managing Agent"
SEVEN_A_CHECKBOX = "Check Box16"
MOVE_IN_DATE_FIELDS = ("2 I moved into my apartment on", "undefined", "undefined_2")
APARTMENTS_FIELD = "3 The total number of apartments in this building is"
VOUCHER_NUMBER_FIELD = "If applicable enter CertificateVoucher Number"

REGULATION_CHECKBOXES = {
    "rent_stabilized": "Check Box11",
    "rent_controlled": "Check Box13",
    "hotel_stabilized": "Check Box14",
    "sro": "Check Box15",
}
SCRIE_CHECKBOXES = {"yes": "Check Box17", "no": "Check Box18"}
SECTION8_CHECKBOXES = {
    "none": "Check Box19",
    "hud": "Check Box20",
    "nycha": "Check Box21",
    "housing_choice_voucher": "Check Box22",
    "hpd": "Check Box23",
}

# ---- page 2
NOTICE_DATE_FIELDS = (
    "4 The conditions noted in this application were brought to the attention of the owner or agent by letter on",
    "undefined_3",
    "undefined_4",
)
NOTICE_METHOD_CHECKBOXES = {
    "regular_mail": "Check Box24",
    "certified_mail": "Check Box25",
    "personal": "Check Box26",
}

# (key, label, checkbox, first line, continuation line, first-line width pt, continuation width pt)
ROOMS = (
    ("kitchen", "Kitchen", "Check Box10", "Kitchen", "undefined_5", 426, 469),
    ("bathroom", "Bathroom", "Check Box27", "Bathroom", "undefined_7", 416, 468),
    ("bedroom", "Bedroom", "Check Box28", "Bedroom Specify which bedroom if more than one", "undefined_9", 247, 470),
    ("living_room", "Living room", "Check Box29", "Living Room", "undefined_11", 410, 470),
    ("dining_room", "Dining room", "Check Box30", "Dining Room", "undefined_13", 408, 471),
    ("hall", "Hall inside apartment", "Check Box31", "Hall Inside Apartment", "undefined_15", 372, 470),
    ("other", "Other", "Check Box32", "Other Specify which room and the problem", "Part III  Tenants Affirmation", 281, 470),
)
ROOM_KEYS = tuple(room[0] for room in ROOMS)
ROOM_FONT_SIZE = 11  # the room lines' own /DA

# Kept for callers and tests written against the first version.
COMPLAINT_FIELD = "Other Specify which room and the problem"
COMPLAINT_OVERFLOW_FIELD = "Part III  Tenants Affirmation"

CONTINUED = " - continued on attached page"
DEFAULT_FONT_SIZE = 10  # for fields whose /DA says "auto" (0 Tf)

# "... Brooklyn, NY 11216", "... Apt 4B, Bronx, NY 10453"
_CITY_STATE_ZIP = re.compile(r",\s*([^,]+,\s*(?:NY|New York)\s*\d{5}(?:-\d{4})?)\s*$", re.I)
_APARTMENT = re.compile(r"\b(?:apt\.?|apartment|unit|#)\s*([A-Za-z0-9\-]+)", re.I)

# Helvetica advance widths (1/1000 em) for ASCII 32-126, from the standard
# AFM metrics. Used to wrap text to the width a line really has.
_HELVETICA = [
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556,
    556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778,
    722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278,
    278, 278, 469, 556, 333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
    556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584,
]


def text_width(text: str, size: float) -> float:
    """Width of text in Helvetica at `size` points (non-ASCII counted wide)."""
    total = 0
    for ch in text:
        code = ord(ch)
        total += _HELVETICA[code - 32] if 32 <= code <= 126 else 667
    return total * size / 1000


def wrap(text: str, widths: list[float], size: float = ROOM_FONT_SIZE) -> tuple[list[str], str]:
    """Fill lines of the given widths word by word; return (lines, leftover)."""
    words = (text or "").split()
    lines: list[str] = []
    for width in widths:
        line = ""
        while words:
            candidate = f"{line} {words[0]}".strip()
            if text_width(candidate, size) <= width:
                line = candidate
                words.pop(0)
            elif not line:
                # One word wider than the whole line: break it.
                word = words.pop(0)
                cut = len(word)
                while cut > 1 and text_width(word[:cut], size) > width:
                    cut -= 1
                line, rest = word[:cut], word[cut:]
                if rest:
                    words.insert(0, rest)
                break
            else:
                break
        lines.append(line)
    return lines, " ".join(words)


def split_address(address: str) -> tuple[str, str, str]:
    """Best-effort split of one free-text address into (street, apt, city/state/zip).

    Anything this cannot confidently separate stays in the street line
    rather than being dropped or guessed into the wrong box.
    """
    address = (address or "").strip()
    if not address:
        return "", "", ""

    city_state_zip = ""
    match = _CITY_STATE_ZIP.search(address)
    if match:
        city_state_zip = match.group(1).strip()
        address = address[: match.start()].strip().rstrip(",")

    apartment = ""
    apt_match = _APARTMENT.search(address)
    if apt_match:
        apartment = apt_match.group(1).strip()
        address = (address[: apt_match.start()] + address[apt_match.end():]).strip().rstrip(",").strip()

    return address, apartment, city_state_zip


def _text(value) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return ""
    return " ".join(str(value).split())


def _choice(value, allowed) -> str:
    value = _text(value).lower().replace(" ", "_").replace("-", "_")
    return value if value in allowed else ""


def parse_date(value) -> tuple[str, str, str]:
    """(month, day, year) from YYYY-MM-DD, YYYY-MM or MM/DD/YYYY; blanks for parts not given."""
    value = _text(value)
    match = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?", value)
    if match:
        year, month, day = match.group(1), match.group(2), match.group(3) or ""
    else:
        match = re.fullmatch(r"(\d{1,2})/(?:(\d{1,2})/)?(\d{4})", value)
        if not match:
            return "", "", ""
        month, day, year = match.group(1), match.group(2) or "", match.group(3)
    try:
        date(int(year), int(month), int(day or 1))
    except ValueError:
        return "", "", ""
    return f"{int(month):02d}", (f"{int(day):02d}" if day else ""), year


def parse_intake(reply: str) -> dict | None:
    """The completion JSON from an assistant reply, or None if it isn't one.

    Tolerates the two ways models actually wrap it: a ```json fence, or a
    sentence before the object.
    """
    if not reply or '"status"' not in reply:
        return None
    text = reply.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    elif not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        text = text[start:end + 1]
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("status") != "complete":
        return None
    return data


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def normalize_intake(data: dict) -> dict:
    """The intake in its current shape, from either the old or the new JSON.

    Version 1 was {"name", "address", "complaint"}; it is mapped onto the
    tenant block and the "Other" room so nothing it carried is lost.
    """
    data = _dict(data)
    tenant, owner = _dict(data.get("tenant")), _dict(data.get("owner"))
    coop, notice = _dict(data.get("coop_condo")), _dict(data.get("notice"))
    conditions = _dict(data.get("conditions"))

    street, apt, city = _text(tenant.get("street")), _text(tenant.get("apt")), _text(tenant.get("city_state_zip"))
    legacy_address = _text(tenant.get("address")) or _text(data.get("address"))
    if not (street or city) and legacy_address:
        street, split_apt, city = split_address(legacy_address)
        apt = apt or split_apt

    owner_street, owner_city = _text(owner.get("street")), _text(owner.get("city_state_zip"))
    if not (owner_street or owner_city) and owner.get("address"):
        owner_street, _, owner_city = split_address(_text(owner.get("address")))

    rooms = {key: _text(conditions.get(key)) for key in ROOM_KEYS}
    legacy_complaint = _text(data.get("complaint"))
    if legacy_complaint and not any(rooms.values()):
        rooms["other"] = legacy_complaint

    return {
        "tenant": {
            "name": _text(tenant.get("name")) or _text(data.get("name")),
            "street": street,
            "apt": apt,
            "city_state_zip": city,
            "phone_day": _text(tenant.get("phone_day")),
            "phone_home": _text(tenant.get("phone_home")),
        },
        "owner": {
            "name": _text(owner.get("name")),
            "street": owner_street,
            "city_state_zip": owner_city,
            "phone": _text(owner.get("phone")),
        },
        "subject_building": _text(data.get("subject_building")),
        "regulation": _choice(data.get("regulation"), REGULATION_CHECKBOXES),
        "coop_condo": {
            "unit_owner": _text(coop.get("unit_owner")),
            "corporation": _text(coop.get("corporation")),
            "managing_agent": _text(coop.get("managing_agent")),
        },
        "seven_a_administrator": data.get("seven_a_administrator") is True,
        "move_in_date": parse_date(data.get("move_in_date")),
        "apartments_in_building": _text(data.get("apartments_in_building")),
        "scrie_drie": _choice(data.get("scrie_drie"), SCRIE_CHECKBOXES),
        "section8": _choice(data.get("section8"), SECTION8_CHECKBOXES),
        "voucher_number": _text(data.get("voucher_number")),
        "notice": {
            "date": parse_date(notice.get("date")),
            "method": _choice(notice.get("method"), NOTICE_METHOD_CHECKBOXES),
        },
        "conditions": rooms,
    }


def missing_fields(intake: dict) -> list[str]:
    """What the RA-81 can't be filed without, in plain language. Empty when ready."""
    missing = []
    tenant, owner = intake["tenant"], intake["owner"]
    if not tenant["name"]:
        missing.append("your full name")
    if not tenant["street"] or not tenant["city_state_zip"]:
        missing.append("your full mailing address (street, apartment, city, state and ZIP)")
    if not owner["name"]:
        missing.append("your landlord's or managing agent's name")
    if not owner["street"] or not owner["city_state_zip"]:
        missing.append("your landlord's mailing address")
    if not intake["regulation"]:
        missing.append("whether your apartment is rent stabilized, rent controlled, hotel stabilized or an SRO")
    if not any(intake["conditions"].values()):
        missing.append("which room each problem is in, and what the problem is")
    return missing


def _pdf_string(text: str) -> str:
    """A PDF literal string in WinAnsi (cp1252), escaped."""
    text = text.encode("cp1252", "replace").decode("cp1252")
    return "(" + text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"


def _helvetica() -> DictionaryObject:
    return DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"), NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
    })


def fitted_size(text: str, width: float, height: float, base: float) -> float:
    """The largest size up to `base` at which text fits the box."""
    size = min(base, max(height - 4, 4))
    while size > 4 and text_width(text, size) > width - 4:
        size -= 0.25
    return round(size, 2)


def _apply_values(writer: PdfWriter, text: dict, checks: set) -> None:
    """Write values, font sizes and appearance streams ourselves.

    pypdf's own appearance generation differs between versions (3.x
    escaped parentheses twice, so "(rear)" printed as "\\(rear\\)") and
    can't shrink text to fit, so a long street ran under "Apt. No.". Each
    filled text field gets an explicit /DA size that fits its box and an
    appearance stream drawn with it, and NeedAppearances is
    switched off so every viewer shows exactly these.
    """
    helvetica = writer._add_object(_helvetica())
    for page in writer.pages:
        annots = page.get("/Annots")
        for ref in (annots.get_object() if annots is not None else []):
            widget = ref.get_object()
            name = widget.get("/T")
            if name is None and widget.get("/Parent") is not None:
                name = widget["/Parent"].get_object().get("/T")
            if name is None:
                continue
            field = widget if widget.get("/T") is not None else widget["/Parent"].get_object()
            if name in checks:
                field[NameObject("/V")] = NameObject("/Yes")
                widget[NameObject("/AS")] = NameObject("/Yes")
                continue
            if name not in text:
                continue
            value = text[name]
            x1, y1, x2, y2 = (float(v) for v in widget["/Rect"])
            width, height = abs(x2 - x1), abs(y2 - y1)
            da = str(widget.get("/DA") or field.get("/DA") or "")
            match = re.search(r"([\d.]+)\s+Tf", da)
            base = float(match.group(1)) if match and float(match.group(1)) > 0 else DEFAULT_FONT_SIZE
            size = fitted_size(value, width, height, base)
            field[NameObject("/V")] = TextStringObject(value)
            widget[NameObject("/DA")] = TextStringObject(f"/Helv {size} Tf 0 g")
            baseline = max(2.0, (height - size) / 2 + size * 0.22)
            stream = DecodedStreamObject()
            stream.set_data(
                (f"/Tx BMC q 1 1 {width - 2:.2f} {height - 2:.2f} re W n BT /Helv {size} Tf 0 g "
                 f"2 {baseline:.2f} Td {_pdf_string(value)} Tj ET Q EMC").encode("cp1252", "replace")
            )
            stream.update({
                NameObject("/Type"): NameObject("/XObject"),
                NameObject("/Subtype"): NameObject("/Form"),
                NameObject("/BBox"): ArrayObject([FloatObject(0), FloatObject(0), FloatObject(width), FloatObject(height)]),
                NameObject("/Resources"): DictionaryObject({
                    NameObject("/Font"): DictionaryObject({NameObject("/Helv"): helvetica}),
                }),
            })
            widget[NameObject("/AP")] = DictionaryObject({NameObject("/N"): writer._add_object(stream)})


def _attachment_pages(writer: PdfWriter, intake: dict, overflow: list[tuple[str, str]]) -> None:
    """Append plain pages with every room description that didn't fit, in full."""
    width, height, margin, size, leading = 612, 792, 54, 11, 15
    usable = width - 2 * margin
    tenant = intake["tenant"]
    address = ", ".join(part for part in (
        tenant["street"], f"Apt. {tenant['apt']}" if tenant["apt"] else "", tenant["city_state_zip"]) if part)
    lines: list[tuple[str, int, str]] = [
        ("Attachment to Form RA-81 - Part II, Description of Decreased Service(s), continued", 12, "F2"),
        (f"Tenant: {tenant['name']}", size, "F1"),
        (f"Address: {address}", size, "F1"),
        ("", size, "F1"),
    ]
    for label, full_text in overflow:
        lines.append((f"{label}:", size, "F2"))
        remaining = full_text
        while remaining:
            (line,), remaining = wrap(remaining, [usable], size)
            if not line:
                break
            lines.append((line, size, "F1"))
        lines.append(("", size, "F1"))

    pages, ops, y = [], [], height - margin
    for text, font_size, font in lines:
        if y < margin + leading:
            pages.append(ops)
            ops, y = [], height - margin
        if text:
            ops.append(f"BT /{font} {font_size} Tf {margin} {y} Td {_pdf_string(text)} Tj ET")
        y -= leading
    pages.append(ops)

    def font(base: str) -> DictionaryObject:
        return DictionaryObject({
            NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject(base), NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
        })

    fonts = DictionaryObject({NameObject("/F1"): font("/Helvetica"), NameObject("/F2"): font("/Helvetica-Bold")})
    for ops in pages:
        page = writer.add_blank_page(width, height)
        stream = DecodedStreamObject()
        stream.set_data("\n".join(ops).encode("cp1252", "replace"))
        page[NameObject("/Contents")] = writer._add_object(stream)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): fonts})
        page[NameObject("/Annots")] = ArrayObject()


def field_values(intake: dict) -> tuple[dict, set, list[tuple[str, str]]]:
    """(text values, checkboxes to tick, room descriptions that need the attachment)."""
    tenant, owner, coop = intake["tenant"], intake["owner"], intake["coop_condo"]
    text = {
        TENANT_NAME_FIELD: tenant["name"],
        TENANT_STREET_FIELD: tenant["street"],
        TENANT_APT_FIELD: tenant["apt"],
        TENANT_CITY_STATE_ZIP_FIELD: tenant["city_state_zip"],
        TENANT_PHONE_DAY_FIELD: tenant["phone_day"],
        TENANT_PHONE_HOME_FIELD: tenant["phone_home"],
        OWNER_NAME_FIELD: owner["name"],
        OWNER_STREET_FIELD: owner["street"],
        OWNER_CITY_STATE_ZIP_FIELD: owner["city_state_zip"],
        OWNER_PHONE_FIELD: owner["phone"],
        SUBJECT_BUILDING_FIELD: intake["subject_building"],
        COOP_UNIT_OWNER_FIELD: coop["unit_owner"],
        COOP_CORPORATION_FIELD: coop["corporation"],
        COOP_MANAGING_AGENT_FIELD: coop["managing_agent"],
        APARTMENTS_FIELD: intake["apartments_in_building"],
        VOUCHER_NUMBER_FIELD: intake["voucher_number"],
    }
    text.update(zip(MOVE_IN_DATE_FIELDS, intake["move_in_date"]))
    text.update(zip(NOTICE_DATE_FIELDS, intake["notice"]["date"]))

    checks = set()
    if intake["regulation"]:
        checks.add(REGULATION_CHECKBOXES[intake["regulation"]])
    if any(coop.values()):
        checks.add(COOP_CHECKBOX)
    if intake["seven_a_administrator"]:
        checks.add(SEVEN_A_CHECKBOX)
    if intake["scrie_drie"]:
        checks.add(SCRIE_CHECKBOXES[intake["scrie_drie"]])
    if intake["section8"]:
        checks.add(SECTION8_CHECKBOXES[intake["section8"]])
    if intake["notice"]["method"]:
        checks.add(NOTICE_METHOD_CHECKBOXES[intake["notice"]["method"]])

    overflow = []
    for key, label, checkbox, first, second, first_width, second_width in ROOMS:
        description = intake["conditions"][key]
        if not description:
            continue
        checks.add(checkbox)
        widths = [first_width - 4, second_width - 4]
        (line1, line2), rest = wrap(description, widths)
        if rest:
            # Make room for the pointer on the second line; the attachment
            # then carries the whole description so it reads in one piece.
            widths[1] -= text_width(CONTINUED, ROOM_FONT_SIZE)
            (line1, line2), _ = wrap(description, widths)
            line2 = (line2 + CONTINUED).strip()
            overflow.append((label, description))
        text[first], text[second] = line1, line2

    return {k: v for k, v in text.items() if v}, checks, overflow


class FormService:
    @staticmethod
    def fill_tenant_form(json_data: dict, template_path: str | None = None, output_filename: str | None = None) -> str:
        """Fill the RA-81 and return a path to the completed file.

        Accepts the current intake JSON or the original {name, address,
        complaint}. Every call writes to its own temporary file, so two
        tenants finishing at once can never receive each other's form.
        """
        intake = normalize_intake(json_data)
        text, checks, overflow = field_values(intake)

        writer = PdfWriter(clone_from=template_path or TEMPLATE_PATH)
        # Every filled field gets its own appearance stream (_apply_values),
        # so viewers are told NOT to regenerate them: when they did
        # (NeedAppearances true), Poppler-based viewers redrew every empty
        # checkbox without its square, and auto-sized text overflowed.
        writer._root_object["/AcroForm"][NameObject("/NeedAppearances")] = BooleanObject(False)

        _apply_values(writer, text, checks)

        if overflow:
            _attachment_pages(writer, intake, overflow)

        prefix = os.path.splitext(output_filename or "completed_complaint")[0]
        handle, output_path = tempfile.mkstemp(prefix=f"{prefix}_", suffix=".pdf")
        with os.fdopen(handle, "wb") as stream:
            writer.write(stream)
        return output_path


READY_MESSAGE = (
    "Your RA-81 is filled in and downloading now. Before you send it:\n\n"
    "1. Check every box on both pages against your own records and fix anything that's wrong.\n"
    "2. Sign and date Part III (page 2) by hand.\n"
    "3. Attach a copy of the letter you sent your landlord about these problems, and your proof of "
    "mailing or delivery.\n"
    "4. Mail or deliver the original plus one copy (and one copy of every attachment) to DHCR, "
    "Gertz Plaza, 92-31 Union Hall St., 6th Floor, Jamaica, NY 11433. Keep a copy for yourself."
)


def missing_message(missing: list[str]) -> str:
    """What the tenant is told when the form can't be filled yet."""
    items = "\n".join(f"- {item}" for item in missing)
    return f"Before I can fill in your RA-81, I still need:\n\n{items}"


def fill_to_bytes(json_data: dict) -> bytes:
    """Fill the form and return the PDF bytes; the temporary file is removed."""
    path = FormService.fill_tenant_form(json_data)
    try:
        with open(path, "rb") as handle:
            return handle.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
