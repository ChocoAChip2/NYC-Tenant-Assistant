"""The Resources tab: official places to get help, by borough.

Read-only and curated. Every phone number and address here was checked
against the agency's own page on the date in VERIFIED (owner rule: never
invent an organisation, number or address -- the chat's referral list in
branding.HELP_RESOURCES follows the same rule). Community board offices
come live from the City's community board directory (area_service), so
they are not repeated here.
"""

VERIFIED = "2026-10-07"

COURTS_SOURCE_URL = "https://portal.311.nyc.gov/article/?kanumber=KA-01075"

# NYC Housing Court locations, from NYC311's Housing Court article and the
# Courts' Civil Court address list (both checked 2026-10-07). The Harlem
# Community Justice Center is listed by 311 as closed, so it is left out.
HOUSING_COURTS = {
    "Manhattan": [
        {"name": "Manhattan Housing Court", "address": "111 Centre Street, New York, NY 10013"},
    ],
    "Bronx": [
        {"name": "Bronx Housing Court", "address": "1118 Grand Concourse, Bronx, NY 10456"},
    ],
    "Brooklyn": [
        {"name": "Brooklyn Housing Court", "address": "141 Livingston Street, Brooklyn, NY 11201"},
        {"name": "Red Hook Community Justice Center", "address": "88 Visitation Place, Brooklyn, NY 11231"},
    ],
    "Queens": [
        {"name": "Queens Housing Court", "address": "89-17 Sutphin Boulevard, Jamaica, NY 11435"},
    ],
    "Staten Island": [
        {"name": "Staten Island Housing Court", "address": "927 Castleton Avenue, Staten Island, NY 10310"},
    ],
}

REPORT_LINKS = [
    {
        "name": "Report a repair or heat problem (311)",
        "detail": "Ask HPD to inspect your apartment. Call 311 or file online.",
        "url": "https://portal.311.nyc.gov/",
    },
    {
        "name": "Rent-stabilized apartment questions (NY State)",
        "detail": "Rent history, overcharges and lease renewals.",
        "url": "https://hcr.ny.gov/tenant-resources",
    },
    {
        "name": "Broker fee you shouldn't have paid",
        "detail": "File a complaint with the City's consumer protection department.",
        "url": "https://www.nyc.gov/site/dca/about/FAQ-Broker-Fees.page",
    },
]


def courts_for(borough: str | None) -> list[dict]:
    return [dict(c, url=COURTS_SOURCE_URL) for c in HOUSING_COURTS.get(borough or "", [])]
