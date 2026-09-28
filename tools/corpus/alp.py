"""Parse American Legal Publishing (ALP) bulk XML into `Section`s.

ALP is the city's contracted official publisher of the NYC Administrative
Code. Its bulk export is one XML file per chapter inside
`https://files.amlegal.com/pdffiles/NewYorkCity/Admin/XML.zip`. The
structure, verified on the real files (see docs/legal-sources-catalog.md
and tests/fixtures/alp/):

    <LEVEL style-name="Chapter">            heading-path ancestors carry a
      <RECORD><HEADING>Chapter 2: ...        <HEADING> in their first RECORD;
      <LEVEL style-name="Subchapter">        empty wrapper levels have none
        <LEVEL style-name="Section">
          <RECORD id="0-0-0-60410">          id -> official URL
            <HEADING>§ 27-2029 Minimum ...   citation + title
            <PARA>§ 27-2029 Minimum ...      echo of the heading, skipped
          <LEVEL style-name="Normal Level">
            <RECORD><PARA>...                one paragraph per RECORD

Inside a PARA: <TAB/> is a space, <LINK> is a cross-reference whose text
is part of the law, <CHARFORMAT> is bold/italic, <DESTINATION> and
<BOOKMARK> are anchors with no text, and <HIGHLIGHTER> holds publisher
supplement tags like "[ALP S-017]" that are NOT law and are dropped.

Each paragraph is then sorted into one of three piles, because only one
of them is the law:

  * law text -- hashed, chunked, quoted;
  * history lines, "(Am. L.L. 2017/086, 5/30/2017, eff. 10/1/2017)" --
    kept, and the source of a real per-section "last amended" date;
  * editor's notes (PARA style "EdNoteSm") -- publisher commentary, kept
    for display but never presented as law.

Anything the parser does not recognise is still kept (as law text, the
conservative choice for a verbatim library) but reported in
`ParseResult.warnings`, so a run over the full zip surfaces markup the
fixtures never showed rather than silently mishandling it.
"""

from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from tools.corpus.model import Section

OFFICIAL_URL = "https://codelibrary.amlegal.com/codes/newyorkcity/latest/NYCadmin/{record_id}"

# Elements seen inside PARAs in the real files. Anything else is reported.
_KNOWN_INLINE = {"TAB", "HIGHLIGHTER", "LINK", "CHARFORMAT", "DESTINATION", "BOOKMARK"}
_EDITORS_NOTE_STYLES = {"EdNoteSm"}
# Heading-path levels. "Normal Level" wraps a section's paragraphs; the
# rest are the code's own hierarchy, most of them empty wrappers in ALP.
_BODY_LEVEL = "Normal Level"
_SECTION_LEVEL = "Section"

_HEADING_RE = re.compile(r"^§\s*(?P<citation>\d+(?:-[0-9A-Za-z.]+)+?)\.?\s+(?P<title>.*)$", re.S)
_REPEALED_RE = re.compile(r"\[\s*Repealed\s*\]", re.I)
_WS_RE = re.compile(r"[ \t\r\n]+")

# A history line is one whole paragraph in parentheses that opens with an
# enactment verb or a session-law cite. Real forms, all in the fixtures:
#   (Am. L.L. 2017/086, 5/30/2017, eff. 10/1/2017)
#   (L.L. 2017/136, 8/11/2017, eff. 8/11/2017; Am. L.L. 2023/020, ...)
#   (Repealed L.L. 2018/055, 1/19/2018, eff. 1/19/2019)
#   (Repealed 2019 N.Y. Laws Ch. 36 Pt. D § 5, 6/14/2019, eff. 6/14/2019)
#   (Am. 2019 N.Y. Laws Ch. 36 Pt. K § 15, 6/14/2019, eff. 6/14/2019)
# It must also name a local law or a session law, so a subdivision that
# happens to be wrapped in parentheses is never mistaken for one.
_HISTORY_OPEN_RE = re.compile(
    r"^\(\s*(?:Am\.|Amended|Added|Repealed|Renumbered|Ren\.|Formerly|Derived|Reenacted|"
    r"L\.L\.|\d{4}\s+N\.Y\.\s+Laws)",
    re.I,
)
_HISTORY_CITE_RE = re.compile(r"L\.L\.\s*\d{4}/\d+|N\.Y\.\s+Laws|\bCh\.\s*\d+", re.I)
_HISTORYISH_RE = re.compile(r"L\.L\.\s*\d{4}/\d+|N\.Y\.\s+Laws")


class AlpParseError(ValueError):
    """The XML cannot be turned into a trustworthy set of sections."""


@dataclass
class ParseResult:
    sections: list[Section]
    warnings: list[str] = field(default_factory=list)


def parse_chapter(data: bytes, *, source_key: str, authority: str) -> ParseResult:
    """Parse one ALP chapter file into its sections.

    Raises AlpParseError on markup that makes the result untrustworthy:
    unparseable XML, an entity declaration, or two sections that would
    share a section_key (a real risk: Title 26 has two § 26-1301s, which
    is why the key includes the source).
    """
    if b"<!ENTITY" in data or b"<!DOCTYPE" in data:
        # ALP files carry neither. Refusing them outright closes off entity
        # expansion attacks on a file that arrives over the network.
        raise AlpParseError("refusing XML with a DOCTYPE or entity declaration")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise AlpParseError(f"not well-formed XML: {exc}") from exc

    warnings: list[str] = []
    sections: list[Section] = []
    _walk(root, [], source_key, authority, sections, warnings)

    seen: dict[str, str] = {}
    for section in sections:
        url = section.official_url
        if section.section_key in seen:
            raise AlpParseError(
                f"duplicate section_key {section.section_key} ({seen[section.section_key]} and {url})"
            )
        seen[section.section_key] = url
    # One unknown style repeated 400 times is one finding, not 400.
    return ParseResult(sections=sections, warnings=list(dict.fromkeys(warnings)))


def read_chapter_from_zip(zip_source, file_id: str) -> bytes:
    """Return `<file_id>.xml` from the ALP bulk zip (a path, bytes or file)."""
    if isinstance(zip_source, (bytes, bytearray)):
        zip_source = io.BytesIO(zip_source)
    wanted = f"{file_id}.xml"
    with zipfile.ZipFile(zip_source) as archive:
        matches = [n for n in archive.namelist() if n == wanted or n.endswith("/" + wanted)]
        if len(matches) != 1:
            raise AlpParseError(f"{wanted}: expected exactly one match in the zip, found {len(matches)}")
        return archive.read(matches[0])


def _walk(el, path, source_key, authority, out, warnings):
    if el.tag == "LEVEL":
        style = el.get("style-name", "")
        if style == _SECTION_LEVEL:
            section = _parse_section(el, path, source_key, authority, warnings)
            if section is not None:
                out.append(section)
            return
        heading = _level_heading(el)
        if heading:
            path = path + [heading]
    for child in el:
        _walk(child, path, source_key, authority, out, warnings)


def _level_heading(level) -> str | None:
    record = level.find("RECORD")
    if record is None:
        return None
    heading = record.find("HEADING")
    if heading is None:
        return None
    return _clean_heading(_normalize("".join(heading.itertext())))


def _clean_heading(text: str) -> str:
    # "Chapter 13: Provision of Legal Services in Eviction Proceedings*"
    # The asterisk points at an editor's note; it is not part of the name.
    return text.rstrip("* ").strip()


def _parse_section(level, path, source_key, authority, warnings) -> Section | None:
    records = _section_records(level, warnings)
    if not records:
        warnings.append(f"{source_key}: Section level with no RECORD, skipped")
        return None
    first = records[0]
    record_id = first.get("id", "")
    heading_el = first.find("HEADING")
    heading = _normalize("".join(heading_el.itertext())) if heading_el is not None else ""
    match = _HEADING_RE.match(heading)
    if not match:
        warnings.append(f"{source_key}: unrecognised section heading {heading!r} (record {record_id}), skipped")
        return None

    citation = match.group("citation").rstrip(".")
    raw_title = match.group("title")
    repealed = bool(_REPEALED_RE.search(raw_title))
    title = _REPEALED_RE.sub("", raw_title)
    title = title.strip().rstrip("*").strip().rstrip(".").strip()

    paragraphs: list[str] = []
    history: list[str] = []
    notes: list[str] = []
    for index, record in enumerate(records):
        for para_index, para in enumerate(record.findall("PARA")):
            text = _para_text(para, warnings, citation)
            if not text:
                continue
            if index == 0 and para_index == 0 and text.startswith("§"):
                # The heading, echoed as the section record's first PARA.
                # Skipped by position, not by matching, so a small
                # difference between the two never turns it into law text.
                if _clean_heading(text) != _clean_heading(heading):
                    warnings.append(f"§ {citation}: heading echo differs from HEADING: {text[:80]!r}")
                continue
            style = para.get("style-name")
            if style in _EDITORS_NOTE_STYLES:
                notes.append(text)
            elif _is_history(text):
                history.append(text)
            else:
                if style:
                    warnings.append(f"§ {citation}: unknown PARA style {style!r}, kept as law text")
                if text.startswith("(") and text.endswith(")") and _HISTORYISH_RE.search(text):
                    warnings.append(f"§ {citation}: paragraph looks like history but did not match: {text[:80]!r}")
                paragraphs.append(text)

    # Repeal is read from ALP's "[Repealed]" heading marker only, never
    # inferred from history: "(Repealed and added L.L. ...)" is a real form
    # and describes a section that is very much in force. An empty section
    # without the marker is reported instead, and the refresh gates refuse
    # to load one.
    if not paragraphs and not repealed:
        warnings.append(f"§ {citation}: no law text and not marked repealed")

    return Section(
        source_key=source_key,
        authority=authority,
        citation=citation,
        title=title,
        heading_path=" > ".join(path),
        paragraphs=paragraphs,
        official_url=OFFICIAL_URL.format(record_id=record_id),
        history=history,
        notes=notes,
        repealed=repealed,
    )


def _section_records(level, warnings) -> list:
    """RECORDs belonging to this section, in document order.

    The section's own RECORD first, then those in its body level(s). A
    Section nested inside a Section has never been seen; if it appears it
    is reported and left out rather than merged into its parent's text.
    """
    records = []
    for child in level:
        if child.tag == "RECORD":
            records.append(child)
        elif child.tag == "LEVEL":
            style = child.get("style-name", "")
            if style == _SECTION_LEVEL:
                warnings.append("Section nested inside a Section, not merged")
                continue
            if style != _BODY_LEVEL:
                warnings.append(f"unexpected level {style!r} inside a Section, included")
            records.extend(_records_below(child, warnings))
    return records


def _records_below(level, warnings) -> list:
    out = []
    for child in level:
        if child.tag == "RECORD":
            out.append(child)
        elif child.tag == "LEVEL":
            if child.get("style-name") == _SECTION_LEVEL:
                warnings.append("Section nested inside a Section, not merged")
                continue
            out.extend(_records_below(child, warnings))
    return out


def _para_text(para, warnings, citation) -> str:
    parts: list[str] = []
    if para.text:
        parts.append(para.text)
    for child in para:
        _inline(child, parts, warnings, citation)
    return _normalize("".join(parts))


def _inline(el, parts, warnings, citation):
    tag = el.tag
    if tag == "HIGHLIGHTER":
        pass  # "[ALP S-017]": a publisher supplement tag, not law
    elif tag == "TAB":
        parts.append(" ")
    else:
        if tag not in _KNOWN_INLINE:
            warnings.append(f"§ {citation}: unknown inline element <{tag}>, text kept")
        if el.text:
            parts.append(el.text)
        for child in el:
            _inline(child, parts, warnings, citation)
    if el.tail:
        parts.append(el.tail)


def _normalize(text: str) -> str:
    # XML indentation between elements is not content. Only ASCII
    # whitespace is collapsed; any other character is the publisher's.
    return _WS_RE.sub(" ", text).strip()


def _is_history(text: str) -> bool:
    return (
        text.startswith("(")
        and text.endswith(")")
        and bool(_HISTORY_OPEN_RE.match(text))
        and bool(_HISTORY_CITE_RE.search(text))
    )
