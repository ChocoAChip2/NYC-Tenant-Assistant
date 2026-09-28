"""What a section of law looks like once parsed, independent of its source."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

# Chunks are what retrieval ranks and what the model is shown. A section is
# the citable unit, but some sections are enormous (the HMC's definitions,
# the Human Rights Law's § 8-107), and gemini-embedding-001 caps input at
# 2,048 tokens. Chunks are packed from WHOLE PARAGRAPHS so a subdivision is
# never cut mid-sentence -- a quote from "(2) between the hours of ten p.m.
# and six a.m. ..." has to exist intact in one chunk for the citation
# guard's verbatim check to find it.
MAX_CHUNK_CHARS = 3500


@dataclass
class Section:
    source_key: str
    authority: str
    citation: str
    title: str
    heading_path: str
    paragraphs: list[str]
    official_url: str
    history: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    repealed: bool = False

    @property
    def section_key(self) -> str:
        # Citation numbers are NOT unique across a code: NYC Admin Code
        # Title 26 has two Chapter 13s and two § 26-1301s. The source key
        # (one per chapter) makes the identity unambiguous.
        return f"{self.source_key}:{self.citation}"

    @property
    def text(self) -> str:
        return "\n".join(self.paragraphs)

    @property
    def content_hash(self) -> str:
        """Hash of the LAW TEXT only. History lines and editor's notes are
        excluded, so a publisher re-wording a note is not reported as the
        law changing -- and an amendment always is."""
        return law_hash(self.text)

    @property
    def last_amended(self) -> str | None:
        return latest_effective_date(self.history)

    def chunks(self) -> list[str]:
        return pack_paragraphs(self.paragraphs)

    def as_payload(self) -> dict:
        return {
            "section_key": self.section_key,
            "source_key": self.source_key,
            "authority": self.authority,
            "citation": self.citation,
            "title": self.title,
            "heading_path": self.heading_path,
            "full_text": self.text,
            "history": self.history,
            "notes": self.notes,
            "official_url": self.official_url,
            "repealed": self.repealed,
            "last_amended": self.last_amended,
            "content_hash": self.content_hash,
            "chunks": [{"ordinal": i, "text": c} for i, c in enumerate(self.chunks())],
        }


# ASCII whitespace only, spelled out. The database recomputes this hash
# (corpus_law_hash in supabase/migrations/20260929_legal_library.sql) and
# rejects any write where the two disagree, so both sides must mean the
# same characters. Python's \s would also match U+00A0 and friends, which
# Postgres's does not.
_HASH_WS_RE = re.compile(r"[ \t\n\x0b\x0c\r]+")


def law_hash(text: str) -> str:
    normalized = _HASH_WS_RE.sub(" ", text).strip(" ")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def pack_paragraphs(paragraphs: list[str], limit: int = MAX_CHUNK_CHARS) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(para) > limit:
            if current:
                chunks.append("\n".join(current))
                current, size = [], 0
            chunks.extend(_split_long(para, limit))
            continue
        if size + len(para) + 1 > limit and current:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(para)
        size += len(para) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def _split_long(text: str, limit: int) -> list[str]:
    """A single paragraph longer than a chunk: split at sentence ends."""
    out: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("; ", 0, limit)
        if cut < limit // 2:
            cut = rest.rfind(". ", 0, limit)
        if cut < limit // 2:
            cut = rest.rfind(" ", 0, limit)
        if cut <= 0:
            # No break point at all: hard cut, and keep it within the limit
            # (cut + 1 below would otherwise take limit + 1 characters).
            cut = limit - 1
        out.append(rest[: cut + 1].strip())
        rest = rest[cut + 1 :].strip()
    if rest:
        out.append(rest)
    return out


_EFF_DATE_RE = re.compile(r"eff\.\s*(\d{1,2})/(\d{1,2})/(\d{4})", re.I)
_LL_DATE_RE = re.compile(r"L\.L\.\s*\d{4}/\d+,\s*(\d{1,2})/(\d{1,2})/(\d{4})", re.I)


def latest_effective_date(history: list[str]) -> str | None:
    """Most recent effective date named in the amendment history.

    "(Am. L.L. 2017/086, 5/30/2017, eff. 10/1/2017)" -> 2017-10-01. When a
    line gives no "eff." date, the local law's enactment date is used.
    None when the history names no date at all -- never a guess.
    """
    dates = []
    for line in history:
        found = _EFF_DATE_RE.findall(line) or _LL_DATE_RE.findall(line)
        for month, day, year in found:
            dates.append(f"{int(year):04d}-{int(month):02d}-{int(day):02d}")
    return max(dates) if dates else None
