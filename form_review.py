"""Editable RA-81 review fields and server-side confirmation validation."""

from datetime import date
from copy import deepcopy
import form_service

FIELDS = {
    "Tenant": [("tenant.name", "Full name"), ("tenant.street", "Street address"), ("tenant.apt", "Apartment"),
               ("tenant.city_state_zip", "City, state and ZIP"), ("tenant.phone_day", "Daytime phone"), ("tenant.phone_home", "Home phone")],
    "Owner or agent": [("owner.name", "Owner or agent name"), ("owner.street", "Owner street address"),
                       ("owner.city_state_zip", "Owner city, state and ZIP"), ("owner.phone", "Owner phone")],
    "Apartment": [("subject_building", "Building address, if different"), ("regulation", "Apartment regulation"),
                  ("move_in_date", "Move-in date"), ("apartments_in_building", "Number of apartments in building"),
                  ("scrie_drie", "SCRIE or DRIE"), ("section8", "Section 8 program"), ("voucher_number", "Voucher number"),
                  ("coop_condo.unit_owner", "Co-op or condo unit owner"), ("coop_condo.corporation", "Corporation"),
                  ("coop_condo.managing_agent", "Managing agent"), ("seven_a_administrator", "7A administrator")],
    "Notice to owner": [("notice.date", "Date you notified the owner in writing"), ("notice.method", "How you delivered the notice")],
    "Conditions": [(f"conditions.{r[0]}", r[1]) for r in form_service.ROOMS],
}
CHOICES = {
    "regulation": {"": "I don't know", "market_rate": "Market rate", **{k: k.replace("_", " ").title() for k in form_service.REGULATION_CHECKBOXES}},
    "scrie_drie": {"": "Not specified", "yes": "Yes", "no": "No"},
    "section8": {"": "Not specified", **{k: k.replace("_", " ").upper() for k in form_service.SECTION8_CHECKBOXES}},
    "notice.method": {"": "Not specified", "regular_mail": "Regular mail", "certified_mail": "Certified mail", "personal": "Delivered by hand"},
    "seven_a_administrator": {"false": "No / not specified", "true": "Yes"},
}


def canonical(data):
    data = deepcopy(data)
    for container, key in ((data, "move_in_date"), (data.setdefault("notice", {}), "date")):
        value = container.get(key)
        if isinstance(value, (tuple, list)) and len(value) == 3:
            month, day, year = value
            container[key] = "-".join(p for p in (year, month, day) if p) if year and month else ""
    result = form_service.normalize_intake(data)
    for container, key in ((result, "move_in_date"), (result["notice"], "date")):
        month, day, year = container[key]
        container[key] = "-".join(p for p in (year, month, day) if p) if year and month else ""
    return result


def flatten(intake):
    values = {}
    for fields in FIELDS.values():
        for key, _ in fields:
            parts = key.split(".")
            value = intake.get(parts[0], "")
            if len(parts) == 2:
                value = value.get(parts[1], "") if isinstance(value, dict) else ""
            values[key] = str(value).lower() if isinstance(value, bool) else str(value or "")
    return values


def submitted(data):
    # Rebuild from known editable fields; ignore client-supplied status/form/IDs.
    intake = {}
    errors = []
    for fields in FIELDS.values():
        for key, label in fields:
            value = data.get(key, "").strip()
            limit = 2000 if key.startswith("conditions.") else 250
            if len(value) > limit:
                errors.append(f"{label} is too long (maximum {limit} characters).")
            if key in CHOICES and value not in CHOICES[key]:
                errors.append(f"Choose a valid value for {label.lower()}.")
            if key in {"move_in_date", "notice.date"} and value:
                try:
                    if date.fromisoformat(value + "-01" if len(value) == 7 else value) > date.today():
                        raise ValueError()
                except ValueError:
                    errors.append(f"{label} must be a valid date no later than today.")
            parts = key.split(".")
            if len(parts) == 2:
                intake.setdefault(parts[0], {})[parts[1]] = value
            else:
                intake[key] = value == "true" if key == "seven_a_administrator" else value
    if intake.get("regulation") not in form_service.REGULATION_CHECKBOXES:
        errors.append("RA-81 needs a confirmed rent-regulated apartment. If you are unsure, confirm your status with HCR first.")
    if data.get("service_scope") != "individual_other":
        errors.append("For heat or hot water use HHW-1; for building-wide services use RA-84. Confirm an individual-apartment service issue for RA-81.")
    if data.get("confirmed") != "yes":
        errors.append("Review the details and check the confirmation box before downloading.")
    normalized = canonical(intake)
    missing = form_service.missing_fields(normalized)
    if missing:
        errors.append(form_service.missing_message(missing))
    # Avoid silently replacing unsupported characters in the current PDF font.
    try:
        "".join(flatten(normalized).values()).encode("cp1252")
    except UnicodeEncodeError:
        errors.append("Some characters cannot be printed by this form. Use an accurate Latin-script spelling or download the official blank form to complete it yourself.")
    return normalized, errors
