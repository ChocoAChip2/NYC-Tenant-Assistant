# Legal sources & public data catalog

Compiled 2026-09-28 by a research pass, then spot-verified. Every entry
marked VERIFIED returned real content when fetched. **Unofficial mirrors
are never ingested**: `nycadmincode.readthedocs.io` still states the
pre-2017 overnight heat rule (55°F when <40°F outside; current law is a
flat 62°F), which is exactly how a stale corpus would make the citation
guard *approve* wrong law.

## Legal text

| Source | Official source | Access | Status | Priority |
|---|---|---|---|---|
| **Housing Maintenance Code**, Admin Code T27 Ch2 | American Legal Publishing (ALP), the city's contracted official publisher | **Bulk XML** `https://files.amlegal.com/pdffiles/NewYorkCity/Admin/XML.zip` (65 MB, Last-Modified 2026-09-23), no key. File `XML/0-0-0-60027.xml`. Cross-check: nyc.gov DOB PDF `nyc.gov/assets/buildings/pdf/HousingMaintenanceCode.pdf` (created 2026-04-17) | VERIFIED; § 27-2029 = 62°F in both | P0 |
| **Rent Stabilization Law**, T26 **Ch 4** §26-501–520 (Ch 3 is Rent *Control*) | ALP | Admin XML, `XML/0-0-0-201924.xml` | VERIFIED | P0 |
| **Unlawful Eviction**, T26 Ch 5 §26-521+ | ALP | Admin XML, `XML/0-0-0-47504.xml` | VERIFIED | P0 |
| **Right to Counsel**, T26 Ch 13 §26-1301+ | ALP | Admin XML, `XML/0-0-0-47826.xml`. **Title 26 has TWO Chapter 13s and two §26-1301s** (the other is "Certification of Certain Rent Payment"), so section identity must include the chapter | VERIFIED | P0 |
| **NYC Human Rights Law**, §8-107(5) housing discrimination incl. source of income | ALP | Admin XML, `XML/0-0-0-4607.xml` (Title 8 Ch 1) | VERIFIED | P0 |
| Rent Control, T26 Ch 3 §26-401+ | ALP | Admin XML, `XML/0-0-0-228764.xml` | VERIFIED | P2 |
| **RCNY Title 28** (HPD rules: lead ch 11, detectors ch 12, heat/doors ch 25, which HPD violations cite) | ALP | Bulk XML `https://files.amlegal.com/pdffiles/NewYorkCity/Rules/XML.zip` (60 MB, Last-Modified 2026-09-24) | Reachable; not yet parsed | P1 |
| **RPL Art 7** §§220–238-a (223-b retaliation, 226-c notice, 227-c DV, **235-b habitability**, 235-e, 235-f, 238-a) | NY Senate | Open Legislation API, lawId `RPP`, **free key required** | **LOADED 2026-10-01** (all 55 sections of Art. 7, `nys-rpl-7`) | P0 |
| **Good Cause Eviction**, **RPL Art 6-A §§210–218** | NY Senate | API, `RPP`. §212: "this article shall apply to the city of New York". Sunsets June 15, 2034 | **LOADED 2026-10-01** (`nys-good-cause`) | P0 |
| **RPAPL Art 7** (711, 731–733, 743, 745, 749, 751, 753, 755, 756, 768) | NY Senate | API, lawId `RPA` | **LOADED 2026-10-01** (all 32 sections of Art. 7, `nys-rpapl-7`; Art. 7-A too, `nys-rpapl-7a`) | P0 |
| **GOL §§7-101–7-109** (security deposits; 7-108) | NY Senate | API, lawId `GOB`, article `A7T1` | **LOADED 2026-10-01** (`nys-gol-deposits`) | P0 |
| Multiple Dwelling Law | NY Senate; DOB PDF (8/26/2025) `nyc.gov/assets/buildings/pdf/MultipleDwellingLaw.pdf` | API, lawId `MDW` | VERIFIED | P1 |
| ETPA (L.1974 c.576) | NY Senate | API, lawId `ETP`. §5-a still listed despite HSTPA repeal, so **check each section's `repealed` flag** | VERIFIED | P1 |
| Exec Law §296(5) | NY Senate | API, lawId `EXC` | VERIFIED | P1 |
| **Rent Stabilization Code**, 9 NYCRR 2520–2531 | NYS DOS on Westlaw (`govt.westlaw.com/nycrr`) | HTML only | **BLOCKED.** No official fetchable consolidated source found. Cornell LII is unofficial, do not ingest | P0 but unavailable |
| DHCR Fact Sheets (plain-language) | `hcr.ny.gov/fact-sheet-N`, index `hcr.ny.gov/rental-housing-documents-type` | HTML/PDF | VERIFIED (#4 Lease Renewal 04/2023; #26 Rent Increases 07/2026) | P1 |

### NY Senate Open Legislation API (from its public docs)

- Section: `GET https://legislation.nysenate.gov/api/3/laws/{lawId}/{locationId}?key=KEY`, e.g. `/api/3/laws/RPP/235-B`
- Whole law: `GET /api/3/laws/{lawId}?full=true&date=YYYY-MM-DD` (point-in-time back to Oct 2014)
- Change feeds: `/api/3/laws/updates/{from}/{to}`, `/api/3/laws/{lawId}/updates`; repeals: `/api/3/laws/repealed`
- Fields: `lawId, lawName, locationId, title, docType, docLevelId, activeDate, text, repealed, repealedDate, parentLocationIds`
- Key: free, register at legislation.nysenate.gov. Rate limits undocumented. **Response shape not yet verified live** (needs the key).

### ALP XML structure (verified on 0-0-0-60027.xml)

- `<LEVEL style-name="Section">` → first `<RECORD>` holds `<HEADING>§ 27-2029 Minimum temperature to be maintained. </HEADING>`; the RECORD `id` (e.g. `0-0-0-60410`) builds the official URL `https://codelibrary.amlegal.com/codes/newyorkcity/latest/NYCadmin/<id>`
- Law text = `<PARA>`s in child `<LEVEL style-name="Normal Level">` records. `<TAB/>` → space. `<LINK>` = cross-ref, keep its text.
- **Strip** `<HIGHLIGHTER>` (`[ALP S-017]` publisher supplement tags).
- **Exclude from law text, keep separately**: paragraphs styled `EdNoteSm` ("Editor's note:").
- **History lines** such as `(Am. L.L. 2017/086, 5/30/2017, eff. 10/1/2017)` sit as their own PARA at the end of a section, sometimes `justify="center"`. These give a real per-section "last amended" date. Keep them out of the hashed law text.
- Ancestors (`Chapter`/`Subchapter`/`Article`/`Subarticle` levels) give the heading path.

## Public data (NYC Open Data, all keyless)

| Dataset | ID | What a tenant learns | Join key |
|---|---|---|---|
| HPD HMC Violations (**in use**) | `wvxf-dwi5` | Open violations by class | **`bbl` is null on ~0.1–0.2% of rows, clustered by building.** Always OR with `boroid`+`block`+`lot` (100% filled); see `building_service._building_where` |
| HPD Complaints & Problems | `ygpa-z7cr` | Complaint history (NO HEAT…) and outcome | `bbl`, `bin` |
| Multiple Dwelling Registrations | `tesw-yqqr` | Registration status/expiry | `bin`, boro/block/lot → `registrationid` |
| Registration Contacts | `feu5-w2e2` | **Owner, head officer, managing agent** ("who is my landlord") | `registrationid` |
| Housing Litigations | `59kj-x8nc` | HP actions, **harassment findings**, penalties | `bbl`, `bin` |
| AEP Buildings (worst-maintained list) | `hcir-3275` | Building is on HPD's AEP | `bbl` |
| CONH pilot list | `bzxi-2tsw` | Certification of No Harassment required | `bbl` |
| Speculation Watch List | `adax-9mit` | Speculative sale of a rent-regulated building | `bbl` |
| HPD Vacate Orders | `tb8q-a3ar` | Vacate orders | `bbl` |
| DOB Violations (BIS) / Safety Violations (DOB NOW) | `3h2n-5cm9` / `855j-jady` | Building-code violations | `bin` |
| DOB ECB Violations | `6bgk-3dad` | ECB/OATH penalties | `bin` |
| DOB Complaints | `eabe-havv` | Illegal conversions etc. | `bin` |
| 311 Service Requests | `erm2-nwe9` | `complaint_type='HEAT/HOT WATER'` history | `bbl` |
| Marshal-executed evictions | `6z8x-wfk4` | Evictions at the address | `bbl` |

**Rent-stabilized building lists** (≥1 stabilized unit; not which apartments): RGB-hosted HCR PDFs per borough, e.g. `rentguidelinesboard.cityofnewyork.us/wp-content/uploads/2025/12/2024-DHCR-Bldg-File-Bronx.pdf` (2024 registrations as of Nov 2025).

**RGB Order #58** (adopted 2026-06-25): **0% for 1-year and 0% for 2-year renewals** for leases starting 2026-10-01 to 2027-09-30. PDF `rentguidelinesboard.cityofnewyork.us/wp-content/uploads/2026/06/2026-Apt-Order-58.pdf`. Changes every June.
