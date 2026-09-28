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
    """A New York State law from the Senate's Open Legislation API.

    Listed so the plan is in one place, but NOT loadable yet: the API
    needs a key (NYSENATE_API_KEY) and its response shape has only been
    read from the docs, never seen live. The adapter gets written after a
    real response has been captured, not before.
    """

    key: str
    name: str
    law_id: str
    locations: tuple[str, ...]
    authority: str
    enabled: bool = False
    jurisdiction: str = "NYS"


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
        real_count=37,
    ),
    AlpSource(
        # File ID verified (catalog); section count not yet measured. Set
        # real_count from the first full-zip parse, then enable.
        key="nyc-rent-control",
        name="Rent Control (Admin Code Title 26, Chapter 3)",
        file_id="0-0-0-228764",
        anchors=("26-401",),
        real_count=None,
        enabled=False,
    ),
)

NYS_SOURCES: tuple[NysSource, ...] = (
    NysSource(
        key="nys-rpl-7",
        name="Real Property Law Article 7 (landlord and tenant)",
        law_id="RPP",
        locations=("223-B", "226-C", "227-C", "235-B", "235-E", "235-F", "238-A"),
        authority="NY Real Property Law",
    ),
    NysSource(
        key="nys-good-cause",
        name="Good Cause Eviction (Real Property Law Article 6-A, §§ 210-218)",
        law_id="RPP",
        locations=tuple(str(n) for n in range(210, 219)),
        authority="NY Real Property Law",
    ),
    NysSource(
        key="nys-rpapl-7",
        name="RPAPL Article 7 (summary proceedings)",
        law_id="RPA",
        locations=("711", "731", "732", "733", "743", "745", "749", "751", "753", "755", "756", "768"),
        authority="NY RPAPL",
    ),
    NysSource(
        key="nys-gol-deposits",
        name="General Obligations Law §§ 7-101 to 7-109 (security deposits)",
        law_id="GOB",
        locations=tuple(f"7-10{n}" for n in range(1, 10)),
        authority="NY General Obligations Law",
    ),
)


def alp_sources(keys: list[str] | None = None) -> list[AlpSource]:
    """Enabled ALP sources, or exactly the named ones (enabled or not)."""
    if not keys:
        return [s for s in ALP_SOURCES if s.enabled]
    by_key = {s.key: s for s in ALP_SOURCES}
    nys = {s.key for s in NYS_SOURCES}
    unknown = [k for k in keys if k not in by_key]
    if unknown:
        hint = " (NYS sources have no adapter yet; see registry.NysSource)" if set(unknown) & nys else ""
        raise KeyError(f"unknown source(s): {', '.join(unknown)}{hint}")
    return [by_key[k] for k in keys]
