# American Legal Publishing XML: real excerpts for parser tests

Excerpts of the official NYC Administrative Code XML published by American
Legal Publishing (ALP), the city's contracted publisher:
`https://files.amlegal.com/pdffiles/NewYorkCity/Admin/XML.zip`
(downloaded 2026-09-28; the zip's Last-Modified was 2026-09-23, current
through Local Law 2026/147).

Each file is one chapter with most `Section` levels removed. The kept
sections are **unmodified**: same elements, attributes, publisher tags,
editor's notes and history lines as the original. That's the point, since
parser tests must run against real markup rather than an idealized
imitation. A test suite built on idealized fixtures hid two production bugs
in the building lookup (see `log/2026-09-28-building-lookup-real-data-fixes.txt`).

| File | Chapter | Sections kept | Full chapter has |
|---|---|---|---|
| `0-0-0-60027.xml` | T27 Ch 2 Housing Maintenance Code | 27-2001, 27-2004 (large, editor's note), 27-2005, 27-2013 (editor's note), 27-2018 (repealed), **27-2029** (62°F; history `Am. L.L. 2017/086 … eff. 10/1/2017`), 27-2031 | 211 |
| `0-0-0-201924.xml` | T26 Ch 4 Rent Stabilization Law | 26-501, 26-504.1 (repealed), 26-517.1 (editor's note) | 25 |
| `0-0-0-47504.xml` | T26 Ch 5 Unlawful Eviction | all 9 | 9 |
| `0-0-0-47826.xml` | T26 Ch 13 Right to Counsel | all 6 (26-1301 heading carries `*` and the duplicate-numbering editor's note) | 6 |
| `0-0-0-4607.xml` | T8 Ch 1 Human Rights | 8-101 (editor's note), 8-103 and 8-104 (repealed). § 8-107 left out (273 KB) | 37 |

The law text itself is public (government edicts). These are small
excerpts for tests only. **The full zip is never committed.** The refresh
job downloads it at run time.
