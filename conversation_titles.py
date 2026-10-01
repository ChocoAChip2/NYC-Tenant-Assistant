"""Naming a conversation from its opening exchange.

WHY THIS EXISTS
"+ New chat" creates a conversation titled "New conversation", and before
this nothing ever changed it: a sidebar of ten chats read "New
conversation" ten times. (Auto-naming was written on the unmerged
feat/ai/model-fallback-and-titles branch in August and never reached main.)
Now, after the first reply, a conversation still carrying the default title
is renamed from what the tenant asked. A title the tenant typed, or one a
suggestion chip set ("Repairs and heat"), is never touched.

HOW
1. ai_service.generate_title() asks the cheapest model for a few words.
2. clean() trims whatever comes back to something the sidebar can show,
   or "" when it is unusable.
3. If the model is unavailable or returns nothing usable, from_keywords()
   picks a topic title from the tenant's words ("Heat and hot water").
   If no topic matches, the default title stays and the tenant can still
   rename it from the sidebar menu.

PRIVACY
Titles are stored encrypted, like messages. Even so, the model is told not
to put names, addresses or apartment numbers in a title, and the keyword
fallback only ever produces one of the fixed topic titles below -- never
the tenant's own words -- so a title is safe to show on a shared screen.
"""

from __future__ import annotations

import re

DEFAULT_TITLE = "New conversation"
MAX_WORDS = 6
MAX_CHARS = 60

# Checked in order; the first topic whose pattern matches names the chat.
# Patterns are matched against the lowercased first message.
_TOPICS: tuple[tuple[str, str], ...] = (
    ("RA-81 rent reduction", r"\bra-?81\b|rent reduction|decreased services?"),
    ("Eviction case", r"\bevict|housing court|court papers|petition|marshal|notice of petition|holdover|nonpayment"),
    ("Heat and hot water", r"\bheat|\bheating|hot water|\bboiler|\bradiator|\bcold apartment"),
    ("Mold and leaks", r"\bmold|\bmould|\bleak|water damage|\bceiling\b.*\b(drip|collaps)"),
    ("Pests", r"\bmice\b|\bmouse\b|\brats?\b|roach|bed ?bugs?|\bpests?\b|\bvermin"),
    ("Security deposit", r"security deposit|\bdeposit\b"),
    ("Rent increase", r"rent increase|raise (my|the) rent|raising (my|the) rent|lease renewal|renewal lease"),
    ("Rent stabilization", r"rent[- ]stabiliz|rent[- ]control|preferential rent|overcharge"),
    ("Good Cause Eviction", r"good cause"),
    ("Landlord harassment", r"harass|lock(ed)? me out|illegal(ly)? lock|changed the locks|threaten"),
    ("Gas or utility shutoff", r"\bgas\b|no electricity|power (is )?(off|out)|utilit(y|ies) (off|shut)"),
    ("Repairs", r"\brepair|\bbroken\b|\bfix\b|\bfixed\b|not working|super(intendent)?\b"),
    ("Lease questions", r"\blease\b|sublet|roommate|break(ing)? (my|the) lease"),
    ("Discrimination", r"discriminat|section 8|voucher|source of income|disabilit|accommodation"),
    ("Broker fee", r"broker('s)? fee|fare act"),
)


def is_default(title: str | None) -> bool:
    return (title or "").strip() == DEFAULT_TITLE


def clean(raw: str | None) -> str:
    """A model's answer trimmed to a sidebar title, or "" if unusable."""
    title = (raw or "").strip().splitlines()[0] if (raw or "").strip() else ""
    title = title.strip().strip("\"'“”‘’`*#")
    title = re.sub(r"^(title|conversation title)\s*:\s*", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+", " ", title).strip(" .!?,;:\"'“”‘’`*")
    if not title:
        return ""
    words = title.split(" ")
    if len(words) > MAX_WORDS:
        title = " ".join(words[:MAX_WORDS])
    title = title[:MAX_CHARS].strip()
    if is_default(title) or not re.search(r"[A-Za-z]", title):
        return ""
    return title[0].upper() + title[1:]


def from_keywords(text: str | None) -> str:
    """A fixed topic title for the tenant's message, or "" if none fits."""
    lowered = (text or "").lower()
    for title, pattern in _TOPICS:
        if re.search(pattern, lowered):
            return title
    return ""
