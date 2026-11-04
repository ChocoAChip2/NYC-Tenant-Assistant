"""A printable summary of one chat, as a PDF the tenant downloads.

WHY THIS EXISTS (owner, 2026-11-01)
After a chat a tenant should be able to log off and still act on it: what
was discussed, what to do next, where the assistant isn't sure and who to
ask instead, and links to check everything. docs/product-pdf-summary-report.md
has the full proposal; this is step 1 (download). Email comes later.

WHAT KEEPS IT HONEST
  * The model only writes the summary text (issues, next steps, unsure
    points). It never supplies a link, a phone number or an organisation:
    links come from the conversation's own replies, and only from official
    or listed help sites (OFFICIAL_HOSTS); the help list is
    branding.HELP_RESOURCES, the same list the chat gives.
  * Every number or section sign in a summary line has to appear in the
    conversation itself (or in the help list). A line that fails is
    dropped, and the PDF says that something was left out and why. This
    is the same rule citation_guard applies to numbers in replies: a
    remembered deadline or dollar amount is the likeliest thing to be wrong.
  * The "not sure" section is never empty. The standing line that this is
    general information, not legal advice, is always first.
  * Nothing is stored. The PDF is built for the request and sent.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from fontTools.ttLib import TTFont
from fpdf import FPDF
from flask_babel import gettext

import branding

MAX_ITEMS = 8
MAX_UNSURE = 6
MAX_ITEM_CHARS = 400
MAX_SOURCES = 20
MAX_TRANSCRIPT_CHARS = 30000

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
FONT_REGULAR = os.path.join(FONT_DIR, "DejaVuSans.ttf")
FONT_BOLD = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")

# Links in a reply are kept only when they point at the government or a
# listed help organisation. A reply can quote a link the tenant pasted;
# that is not a source.
OFFICIAL_HOSTS = frozenset({
    "nyc.gov", "ny.gov", "nysenate.gov", "nycourts.gov", "cityofnewyork.us",
    "codelibrary.amlegal.com", "hpdonline.nyc.gov",
}) | frozenset(urlparse(r["url"]).hostname.removeprefix("www.") for r in branding.HELP_RESOURCES if r.get("url"))

# Languages whose letters the bundled font has. Other page languages get
# the summary in English (see languages_for).
FONT_LANGUAGES = {"en": "English", "es": "Spanish", "fr": "French", "sw": "Swahili"}

INSTRUCTION = (
    "You write a short printable summary of one conversation between a New York City "
    "tenant and a tenant-help assistant. Answer with JSON only, in this shape:\n"
    '{"issues": ["..."], "next_steps": ["..."], "unsure": [{"point": "...", "ask": "..."}]}\n'
    "issues: the housing problems the tenant raised, one short sentence each.\n"
    "next_steps: concrete actions the tenant could take, in order, one sentence each, "
    "taken only from what was said in the conversation.\n"
    "unsure: every point the conversation did not settle, anything that depends on facts "
    "the tenant didn't give, and anything about court, eviction, deadlines, money owed or "
    "legal rights. 'point' says what is uncertain; 'ask' names who can answer it, choosing "
    "from: 311, HPD, the NYC Tenant Helpline, Housing Court Answers, NYC Right to Counsel, "
    "the NY State rent office (HCR), a tenant attorney or legal aid.\n"
    "Rules: use only what is in the conversation. Never add a law, section number, date, "
    "deadline, dollar amount, temperature or phone number that the conversation doesn't "
    "contain. Never state anything more certainly than the conversation did. Don't write "
    "the tenant's name, address, apartment, phone number or email. Plain text only: no "
    "markdown, no links. Write in {language}."
)


@dataclass
class Summary:
    issues: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    unsure: list[tuple[str, str]] = field(default_factory=list)
    left_out: int = 0


@dataclass(frozen=True)
class Source:
    label: str
    url: str


def instruction(language_name: str) -> str:
    return INSTRUCTION.replace("{language}", language_name)


def summary_language(page_language: str | None) -> tuple[str, str]:
    """(language code, language name) the summary is written in."""
    code = (page_language or "en").split("_")[0].lower()
    return (code, FONT_LANGUAGES[code]) if code in FONT_LANGUAGES else ("en", "English")


_LINK_RE = re.compile(r"\[([^\]\n]{1,200})\]\((https://[^\s)<>\"']{1,500})\)")


def _plain(text: str) -> str:
    """Markdown links become their text; emphasis and code marks go."""
    text = _LINK_RE.sub(lambda m: m.group(1), text or "")
    return re.sub(r"[*_`#>]+", "", text)


def transcript(messages: list[dict]) -> str:
    """The conversation as the model reads it: newest kept if it's long."""
    lines = []
    for m in messages:
        role = (m.get("role") or "").lower()
        if role not in ("user", "assistant"):
            continue
        content = _plain(str(m.get("content") or "")).strip()
        if content:
            lines.append(("Tenant: " if role == "user" else "Assistant: ") + content)
    text = "\n\n".join(lines)
    return text[-MAX_TRANSCRIPT_CHARS:]


def has_reply(messages: list[dict]) -> bool:
    return any((m.get("role") == "assistant") and str(m.get("content") or "").strip() for m in messages)


def sources(messages: list[dict]) -> list[Source]:
    """Official links the assistant's replies actually gave, first use first."""
    found: dict[str, Source] = {}
    for m in messages:
        if m.get("role") != "assistant":
            continue
        for label, url in _LINK_RE.findall(str(m.get("content") or "")):
            try:
                parsed = urlparse(url)
                host = (parsed.hostname or "").lower().removeprefix("www.")
                if parsed.username or parsed.password or parsed.port not in (None, 443):
                    continue
            except ValueError:
                continue
            if not any(host == h or host.endswith("." + h) for h in OFFICIAL_HOSTS):
                continue
            if url not in found and len(found) < MAX_SOURCES:
                found[url] = Source(_plain(label).strip() or host, url)
    return list(found.values())


_NUMBER_RE = re.compile(r"\d+(?:[.,:/-]\d+)*")


def _numbers(text: str) -> set[str]:
    return set(_NUMBER_RE.findall(text or ""))


def _clean_line(value) -> str:
    text = re.sub(r"\s+", " ", _plain(str(value or ""))).strip()
    if len(text) > MAX_ITEM_CHARS:
        text = text[: MAX_ITEM_CHARS - 1].rsplit(" ", 1)[0] + "…"
    return text


def parse(raw: str, conversation: str) -> Summary | None:
    """The model's JSON, checked. None if it isn't usable at all."""
    try:
        data = json.loads((raw or "").strip().removeprefix("```json").removesuffix("```").strip())
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    allowed = _numbers(conversation) | _numbers(" ".join(r["contact"] for r in branding.HELP_RESOURCES)) | {"311"}
    summary = Summary()

    def checked(text: str) -> str | None:
        if not text:
            return None
        if "§" in text and "§" not in conversation:
            summary.left_out += 1
            return None
        if not _numbers(text) <= allowed:
            summary.left_out += 1
            return None
        return text

    for key, limit in (("issues", MAX_ITEMS), ("next_steps", MAX_ITEMS)):
        items = data.get(key)
        for item in items if isinstance(items, list) else []:
            text = checked(_clean_line(item))
            if text and len(getattr(summary, key)) < limit:
                getattr(summary, key).append(text)
    unsure = data.get("unsure")
    for item in unsure if isinstance(unsure, list) else []:
        if not isinstance(item, dict):
            continue
        point = checked(_clean_line(item.get("point")))
        ask = _clean_line(item.get("ask"))
        if ask and not checked(ask):
            ask = ""
        if point and len(summary.unsure) < MAX_UNSURE:
            summary.unsure.append((point, ask))
    if not (summary.issues or summary.next_steps or summary.unsure):
        return None
    return summary


_glyphs: set[int] = set()


def printable(text: str) -> str:
    """Only characters the bundled font can draw (no emoji, no Chinese)."""
    if not _glyphs:
        _glyphs.update(TTFont(FONT_REGULAR).getBestCmap())
    return "".join(c for c in (text or "") if ord(c) in _glyphs or c in "\n").strip()


class _Pdf(FPDF):
    footer_text = ""

    def footer(self):
        self.set_y(-14)
        self.set_font("Body", size=8)
        self.set_text_color(110, 110, 110)
        self.cell(0, 5, f"{self.footer_text}  ·  {self.page_no()}/{{nb}}", align="C")


def render(summary: Summary, links: list[Source], help_resources: list[dict], *, title: str, made_on: str) -> bytes:
    """The PDF itself. Headings and fixed text come in already translated."""
    pdf = _Pdf(format="Letter")
    pdf.add_font("Body", "", FONT_REGULAR)
    pdf.add_font("Body", "B", FONT_BOLD)
    pdf.footer_text = gettext("%(product)s · %(date)s · General information, not legal advice.",
                              product=branding.PRODUCT_NAME, date=made_on)
    pdf.set_title(gettext("Chat summary"))
    pdf.set_author(branding.PRODUCT_NAME)
    pdf.set_margins(18, 18, 18)
    pdf.set_auto_page_break(True, margin=20)
    pdf.add_page()
    width = pdf.w - pdf.l_margin - pdf.r_margin

    def text(value, size=10.5, bold=False, color=(30, 30, 30), gap=1.5):
        value = printable(value)
        pdf.set_font("Body", "B" if bold else "", size)
        pdf.set_text_color(*color)
        pdf.multi_cell(width, size * 0.5, value, align="L", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(gap)

    def heading(value):
        if pdf.will_page_break(22):
            pdf.add_page()
        pdf.ln(3)
        text(value, size=13, bold=True, color=(28, 93, 140), gap=1)

    def bullets(items, numbered=False):
        for i, item in enumerate(items, 1):
            pdf.set_font("Body", "", 10.5)
            pdf.set_text_color(30, 30, 30)
            pdf.cell(7, 5.25, f"{i}." if numbered else "•")
            pdf.multi_cell(width - 7, 5.25, printable(item), align="L", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1.2)

    text(gettext("Chat summary"), size=18, bold=True, gap=1)
    text(printable(title) or gettext("Your conversation"), size=11, color=(90, 90, 90), gap=0.5)
    text(gettext("Made on %(date)s from your conversation on %(product)s.", date=made_on, product=branding.PRODUCT_NAME),
         size=9.5, color=(90, 90, 90), gap=3)

    pdf.set_fill_color(247, 236, 217)
    pdf.set_draw_color(138, 90, 31)
    pdf.set_font("Body", "", 9.5)
    pdf.set_text_color(90, 60, 20)
    pdf.multi_cell(width, 4.8, gettext(
        "This summary was written by AI from your chat. It is general information, not legal advice, "
        "and it can be incomplete or wrong. Check anything important with the people and links below "
        "before you act on it."), border=1, fill=True, padding=3, align="L", new_x="LMARGIN", new_y="NEXT")

    if summary.issues:
        heading(gettext("What you talked about"))
        bullets(summary.issues)
    if summary.next_steps:
        heading(gettext("Next steps"))
        bullets(summary.next_steps, numbered=True)

    heading(gettext("Where this summary isn't sure: ask a person"))
    unsure_lines = [gettext("Anything about court, eviction, deadlines or money owed: talk to a tenant attorney or legal aid before you act.")]
    for point, ask in summary.unsure:
        unsure_lines.append(f"{point} {gettext('Ask: %(who)s.', who=ask)}" if ask else point)
    if summary.left_out:
        unsure_lines.append(gettext("Some details from your chat were left out because they couldn't be checked. Confirm dates, amounts and deadlines with the people listed below."))
    bullets(unsure_lines)

    heading(gettext("Sources to check"))
    if links:
        for s in links:
            text(s.label, size=10.5, gap=0.3)
            pdf.set_font("Body", "", 9)
            pdf.set_text_color(28, 93, 140)
            pdf.multi_cell(width, 4.5, s.url, link=s.url, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
    else:
        text(gettext("This chat didn't cite a specific law or city record. Use the help contacts above to confirm what applies to you."),
             size=10, color=(70, 70, 70))

    heading(gettext("Where to get help"))
    for r in help_resources:
        text(f"{r['name']}  ·  {r['contact']}", size=10.5, bold=True, gap=0.3)
        text(r.get("detail", ""), size=9.5, color=(70, 70, 70), gap=0.3)
        if r.get("url"):
            pdf.set_font("Body", "", 9)
            pdf.set_text_color(28, 93, 140)
            pdf.multi_cell(width, 4.5, r["url"], link=r["url"], new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    return bytes(pdf.output())
