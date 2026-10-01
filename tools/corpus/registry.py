"""Which law the library holds, where it comes from, and what "looks right" means.

Every source here is checked against sanity gates before anything is
written (see refresh.py): a minimum section count, anchor sections that
must be present, and no empty sections that are not marked repealed. A
parse that fails any of them writes nothing.

Minimums are ~90% of the real section counts in the Admin Code as of the
2026-09-23 ALP export (docs/HANDOFF.md section 0): HMC 211, Rent
Stabilization Law 25, Unlawful Eviction 9, Right to Counsel 6, Human
Rights Law 37. A source whose real count has not been measured is not
enabled -- a guessed minimum is either useless or a false alarm.

State law (NYS_SOURCES) comes from the NY Senate Open Legislation API,
counted from the live API on 2026-10-01.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

ALP_ADMIN_ZIP_URL = "https://files.amlegal.com/pdffiles/NewYorkCity/Admin/XML.zip"
NYC_ADMIN_CODE = "NYC Admin Code"


def ninety_percent(real_count: int) -> int:
    return math.floor(real_count * 0.9)


@dataclass(frozen=True)
class AlpSource:
    """One chapter of the NYC Admin Code, from ALP's bulk XML."""

    key: str
    name: str
    file_id: str
    anchors: tuple[str, ...]
    real_count: int | None
    enabled: bool = True
    authority: str = NYC_ADMIN_CODE
    jurisdiction: str = "NYC"

    @property
    def min_sections(self) -> int | None:
        return ninety_percent(self.real_count) if self.real_count else None


@dataclass(frozen=True)
class NysSource:
    """An article (or title) of a New York State law, from the NY Senate's
    Open Legislation API (tools/corpus/nys.py). Needs NYSENATE_API_KEY.

    Counts were measured from the live API on 2026-10-01; `scope` is the
    path of locationIds from the law down to the article or title.
    """

    key: str
    name: str
    law_id: str
    law_name: str
    scope: tuple[str, ...]
    anchors: tuple[str, ...]
    real_count: int | None
    authority: str
    enabled: bool = True
    jurisdiction: str = "NYS"

    @property
    def min_sections(self) -> int | None:
        return ninety_percent(self.real_count) if self.real_count else None


ALP_SOURCES: tuple[AlpSource, ...] = (
    AlpSource(
        key="nyc-hmc",
        name="Housing Maintenance Code (Admin Code Title 27, Chapter 2)",
        file_id="0-0-0-60027",
        anchors=("27-2001", "27-2004", "27-2005", "27-2029", "27-2031"),
        real_count=211,
    ),
    AlpSource(
        key="nyc-rsl",
        name="Rent Stabilization Law (Admin Code Title 26, Chapter 4)",
        file_id="0-0-0-201924",
        anchors=("26-501", "26-511"),
        real_count=25,
    ),
    AlpSource(
        key="nyc-ue",
        name="Unlawful Eviction (Admin Code Title 26, Chapter 5)",
        file_id="0-0-0-47504",
        anchors=("26-521", "26-523"),
        real_count=9,
    ),
    AlpSource(
        # Title 26 has TWO Chapter 13s and two § 26-1301s; this is the
        # Right to Counsel one, and the source key keeps them apart.
        key="nyc-rtc",
        name="Right to Counsel (Admin Code Title 26, Chapter 13)",
        file_id="0-0-0-47826",
        anchors=("26-1301", "26-1302"),
        real_count=6,
    ),
    AlpSource(
        key="nyc-hrl",
        name="NYC Human Rights Law (Admin Code Title 8, Chapter 1)",
        file_id="0-0-0-4607",
        anchors=("8-101", "8-107"),
        # 37 headings in the full zip, two of them "Reserved." placeholders
        # (§§ 8-108, 8-110) that the parser leaves out.
        real_count=35,
    ),
    AlpSource(
        # Measured on the full zip, 2026-09-28: 22 sections, two of them
        # repealed (§§ 26-403.1, 26-403.2), no parser warnings.
        key="nyc-rent-control",
        name="Rent Control (Admin Code Title 26, Chapter 3)",
        file_id="0-0-0-228764",
        anchors=("26-401", "26-403", "26-408"),
        real_count=22,
    ),
)

NYS_SOURCES: tuple[NysSource, ...] = (
    NysSource(
        key="nys-good-cause",
        name="Good Cause Eviction (Real Property Law Article 6-A, §§ 210-218)",
        law_id="RPP",
        law_name="Real Property Law",
        scope=("A6-A",),
        anchors=("212", "214", "216"),
        real_count=9,
        authority="NY Real Property Law",
    ),
    NysSource(
        key="nys-rpl-7",
        name="Real Property Law Article 7 (landlord and tenant)",
        law_id="RPP",
        law_name="Real Property Law",
        scope=("A7",),
        anchors=("223-b", "226-c", "235-b", "235-e", "238-a"),
        real_count=55,
        authority="NY Real Property Law",
    ),
    NysSource(
        key="nys-rpapl-7",
        name="RPAPL Article 7 (summary proceedings: evictions)",
        law_id="RPA",
        law_name="Real Property Actions and Proceedings Law",
        scope=("A7",),
        anchors=("711", "749", "753", "768"),
        real_count=32,
        authority="NY RPAPL",
    ),
    NysSource(
        key="nys-rpapl-7a",
        name="RPAPL Article 7-A (tenants' proceedings in NYC: 7A administrators)",
        law_id="RPA",
        law_name="Real Property Actions and Proceedings Law",
        scope=("A7-A",),
        anchors=("769", "770", "778"),
        real_count=15,
        authority="NY RPAPL",
    ),
    NysSource(
        key="nys-gol-deposits",
        name="General Obligations Law Article 7 Title 1 (security deposits)",
        law_id="GOB",
        law_name="General Obligations Law",
        scope=("A7", "A7T1"),
        anchors=("7-103", "7-107", "7-108"),
        real_count=7,
        authority="NY General Obligations Law",
    ),
)


def alp_sources(keys: list[str] | None = None) -> list[AlpSource]:
    """Enabled ALP sources, or exactly the named ALP ones (enabled or not)."""
    if not keys:
        return [s for s in ALP_SOURCES if s.enabled]
    by_key = {s.key: s for s in ALP_SOURCES}
    unknown = [k for k in keys if k not in by_key]
    if unknown:
        raise KeyError(f"unknown source(s): {', '.join(unknown)}")
    return [by_key[k] for k in keys]


def all_sources(keys: list[str] | None = None) -> list[AlpSource | NysSource]:
    """Every enabled source (city and state), or exactly the named ones."""
    everything = (*ALP_SOURCES, *NYS_SOURCES)
    if not keys:
        return [s for s in everything if s.enabled]
    by_key = {s.key: s for s in everything}
    unknown = [k for k in keys if k not in by_key]
    if unknown:
        raise KeyError(f"unknown source(s): {', '.join(unknown)}")
    return [by_key[k] for k in keys]
