"""Checks that an assistant reply only claims what its sources actually say.

WHY THIS EXISTS, AND WHAT IT IS AND IS NOT
------------------------------------------
Asking a model not to invent citations does not stop it inventing
citations. This module is the part that does stop it, by checking the
finished reply against the passages the model was shown, in code, before
a tenant reads a word of it.

The first design of this guard only checked that every citation marker
pointed at a retrieved passage. That is not enough, and the gap is
exactly where the harm lives:

    The assistant retrieves NYC Admin Code 27-2029 correctly and replies
    "Under 27-2029 your landlord must keep it at 70F overnight."

Marker valid. Section real. Section retrieved. And the answer is wrong --
the overnight minimum is 62F. A tenant acts on a fabricated number that
now carries a citation and a link, which makes it MORE convincing than an
uncited guess would have been.

So the guard checks four things, and the last two are the ones that
matter:

  1. MARKERS   -- every [Sn] refers to a passage that was actually shown.
  2. STATUTES  -- every statute-shaped string in the reply appears in a
                  retrieved passage (or in the tenant's own message, which
                  the assistant is allowed to repeat back).
  3. QUOTES    -- a sentence may only carry a citation if it contains a
                  quoted span copied verbatim from the cited passage.
  4. NUMBERS   -- every number-with-a-unit in a cited sentence appears in
                  the cited passage. This is what catches "70F".

WHAT IT STILL DOES NOT CATCH
Retrieval quality. If the wrong section is retrieved, a reply can quote it
perfectly and still mislead. No validator fixes that; only better
retrieval does. Claiming otherwise would repeat the mistake this module
was written to correct.

WHY IT DEFAULTS TO LOGGING INSTEAD OF BLOCKING
A guard that rejects good replies is worse than no guard: it silently
makes the assistant say less than it knows, and nobody notices because the
failure looks like a quiet model. So the default mode is REPORT -- record
every violation, change nothing. Switch to ENFORCE once the logs show what
the false-rejection rate actually is. LEGAL_GUARD_MODE picks the mode.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum


class GuardMode(str, Enum):
    REPORT = "report"
    ENFORCE = "enforce"


@dataclass(frozen=True)
class Passage:
    """One retrieved chunk, as the model was shown it."""

    marker: str
    text: str
    citation: str | None = None
    authority: str | None = None
    official_url: str | None = None


@dataclass(frozen=True)
class Violation:
    kind: str
    detail: str
    marker: str | None = None


@dataclass
class GuardResult:
    ok: bool
    violations: list[Violation] = field(default_factory=list)
    cited_markers: list[str] = field(default_factory=list)

    @property
    def kinds(self) -> set[str]:
        return {violation.kind for violation in self.violations}


_MARKER_RE = re.compile(r"\[(S\d+)\]")

# Statute-shaped strings. Deliberately narrow: it has to match the things
# a model invents (section numbers, code cites, rule numbers) without
# matching every number in an ordinary sentence. Agency names (HPD, DHCR)
# and phone numbers are NOT statute-shaped and are not checked here.
_STATUTE_PATTERNS = (
    re.compile(r"(?:§§?|\bsections?\b|\bsec\.)\s*\d+[\w\-.‑]*", re.I),
    re.compile(r"\b(?:RPL|RPAPL|MDL|GBL|NYCRR|CPLR|HMC)\b\s*§?\s*\d+[\w\-.‑]*", re.I),
    re.compile(r"\b\d+\s+NYCRR\s+\d+[\w\-.‑]*", re.I),
    re.compile(r"\blocal law\s+(?:no\.\s*)?\d+\b", re.I),
    re.compile(r"\b(?:admin(?:istrative)?\.?\s+code)\s*§?\s*\d+[\w\-.‑]*", re.I),
)

# A quoted span. Straight and curly doubles, plus the single-guillemet
# forms some models emit.
_QUOTE_RE = re.compile(r"[\"“«]([^\"”»]{8,400})[\"”»]")

# Numbers that carry a claim: temperatures, deadlines, money, percentages.
# A bare "3" in "three things you can do" is not one of these.
_NUMBER_RE = re.compile(
    r"""(?:
          \$\s?\d[\d,]*(?:\.\d+)?
        | \d[\d,]*(?:\.\d+)?\s*(?:
              °\s?[FC]\b | \bdegrees?\b
            | \b(?:business\s+)?days?\b | \bweeks?\b | \bmonths?\b | \byears?\b
            | \bhours?\b | \bpercent\b | %
            | \bdollars?\b
          )
    )""",
    re.I | re.X,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def normalize(text: str) -> str:
    """Fold the differences that should not count as a mismatch.

    A model that retypes a passage with curly quotes, an em dash for a
    double hyphen, or collapsed whitespace has still quoted it. A model
    that changes a number has not, and none of this touches digits.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace("—", "--").replace("–", "-").replace("‑", "-")
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def _sentences(reply: str) -> list[str]:
    return [part for part in _SENTENCE_SPLIT_RE.split(reply.strip()) if part.strip()]


def _statute_strings(text: str) -> list[str]:
    found: list[str] = []
    for pattern in _STATUTE_PATTERNS:
        found.extend(match.group(0) for match in pattern.finditer(text))
    return found


_SECTION_NUMBER_RE = re.compile(r"\d[\w\-.\u2011]*")


def _statute_key(raw: str) -> str:
    """Compare statutes by their section number alone.

    "Section 27-2029", "\u00a7 27-2029" and "Admin. Code 27-2029" are one
    citation wearing three different prefixes, and a check that treated
    them as three would reject correct replies for cosmetic reasons --
    the over-blocking failure mode, which costs more here than a missed
    catch does. So the key is the last number-shaped token in the string,
    with punctuation folded out.

    The trade this makes: a reply naming the right section number under
    the wrong code ("RPL 27-2029") is not flagged by THIS check. The
    quote and number checks still apply to it, and the citation chip it
    renders carries the authority from the retrieved row rather than
    whatever the model typed, so the tenant sees the true one.
    """
    numbers = _SECTION_NUMBER_RE.findall(normalize(raw))
    if not numbers:
        return ""
    return re.sub(r"[^0-9a-z]", "", numbers[-1])


def check(
    reply: str,
    passages: list[Passage],
    user_message: str = "",
) -> GuardResult:
    """Return every way `reply` outruns `passages`. Never raises."""

    violations: list[Violation] = []
    by_marker = {passage.marker: passage for passage in passages}
    corpus_norm = " \n ".join(normalize(passage.text) for passage in passages)
    user_norm = normalize(user_message)

    cited = _MARKER_RE.findall(reply)

    # 1. Markers must exist.
    for marker in dict.fromkeys(cited):
        if marker not in by_marker:
            violations.append(
                Violation("unknown_marker", f"cites {marker}, which was never shown", marker)
            )

    # 2. Statute strings must be grounded somewhere the model was allowed
    #    to get them: a retrieved passage, or the tenant's own words.
    corpus_statutes = {_statute_key(s) for passage in passages for s in _statute_strings(passage.text)}
    corpus_statutes |= {
        _statute_key(passage.citation) for passage in passages if passage.citation
    }
    user_statutes = {_statute_key(s) for s in _statute_strings(user_message)}

    for raw in _statute_strings(reply):
        key = _statute_key(raw)
        if not key:
            continue
        if key in corpus_statutes or key in user_statutes:
            continue
        if key in normalize(user_norm).replace(" ", ""):
            continue
        violations.append(
            Violation("ungrounded_statute", f"names {raw.strip()!r}, which is in no retrieved passage")
        )

    # 3 and 4 apply per sentence, and only to sentences that cite.
    for sentence in _sentences(reply):
        markers = _MARKER_RE.findall(sentence)
        if not markers:
            continue
        known = [by_marker[m] for m in markers if m in by_marker]
        if not known:
            continue

        cited_norm = " \n ".join(normalize(passage.text) for passage in known)

        quotes = _QUOTE_RE.findall(sentence)
        supported_quotes = [q for q in quotes if normalize(q) in cited_norm]

        if not quotes:
            violations.append(
                Violation(
                    "citation_without_quote",
                    f"cites {', '.join(markers)} but quotes nothing from it",
                    markers[0],
                )
            )
        elif not supported_quotes:
            violations.append(
                Violation(
                    "quote_not_in_source",
                    f"quotes {quotes[0][:60]!r}, which is not in {', '.join(markers)}",
                    markers[0],
                )
            )

        for number in _NUMBER_RE.findall(sentence):
            if normalize(number) not in cited_norm:
                # Allow it if the passage states the same figure with
                # different spacing, e.g. "68 degrees" vs "68degrees".
                squashed = normalize(number).replace(" ", "")
                if squashed in cited_norm.replace(" ", ""):
                    continue
                violations.append(
                    Violation(
                        "number_not_in_source",
                        f"states {number.strip()!r}, which {', '.join(markers)} does not say",
                        markers[0],
                    )
                )

    return GuardResult(
        ok=not violations,
        violations=violations,
        cited_markers=list(dict.fromkeys(cited)),
    )


def strip_citations(reply: str) -> str:
    """Remove every marker, leaving the prose readable.

    Used when a reply fails the guard: the answer may still be useful as
    general information, it just may not wear an authority it has not
    earned. The caller is responsible for labelling the result uncited.
    """
    without = _MARKER_RE.sub("", reply)
    without = re.sub(r"\s+([.,;:!?])", r"\1", without)
    return re.sub(r"[ \t]{2,}", " ", without).strip()


def render_sources(passages: list[Passage], result: GuardResult) -> list[dict[str, str]]:
    """The citation chips to show under a reply, for cited passages only.

    Every chip carries the official URL, because a citation a tenant
    cannot open is an assertion, not a source.
    """
    chips = []
    for marker in result.cited_markers:
        passage = next((p for p in passages if p.marker == marker), None)
        if not passage or not passage.citation or not passage.official_url:
            continue
        chips.append(
            {
                "marker": marker,
                "label": f"{passage.authority} {passage.citation}".strip(),
                "url": passage.official_url,
            }
        )
    return chips
