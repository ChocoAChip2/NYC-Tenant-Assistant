# Making SideKick Tidbit the place tenants actually come back to

**Status:** proposal. Nothing here is built. Written 2026-09-28.

## First: the password blocker has a free fix, and it's better

Supabase gates leaked-password protection behind Pro. You don't need it.

Have I Been Pwned's Pwned Passwords range API is **free, needs no API key,
and has no rate limit**. It works by k-anonymity: you send the first 5
characters of the SHA-1 of the password, and it returns every hash suffix
starting with those 5 characters plus a breach count. You match the rest
locally. **The password never leaves your server, and HIBP never learns
which one you checked.**

```python
def times_breached(password: str) -> int:
    digest = hashlib.sha1(password.encode()).hexdigest().upper()
    prefix, suffix = digest[:5], digest[5:]
    body = urlopen(f"https://api.pwnedpasswords.com/range/{prefix}", timeout=3).read().decode()
    for line in body.splitlines():
        candidate, _, count = line.partition(":")
        if candidate == suffix:
            return int(count)
    return 0
```

Wire it into signup, password reset and the settings password change. Two
things matter in how you do it:

- **Fail open.** If HIBP is slow or down, let the signup through. A tenant
  locked out of making an account because a third-party API blinked is a
  worse outcome than a weak password.
- **Warn, don't block, above a threshold.** Blocking every breached
  password frustrates people; blocking the catastrophically common ones
  (say, seen 100+ times) stops the credential-stuffing that actually
  happens.

This is strictly better than the Pro feature, because it runs on *your*
paths and you control the wording and the failure mode.

---

## The real problem

Say it plainly: **most of what the site does today, a tenant could get from
free ChatGPT.** Ask about heat, get a decent answer. The parts that are
genuinely yours are the RA-81 form and the citation work — and the corpus
is still empty.

So the question isn't "what features to add." It's: **what can this do that
a general chatbot structurally cannot?**

There are four answers, and they're ranked.

---

## 1. Know their actual building — the one that changes everything

NYC publishes every HPD housing-maintenance violation as open data. Free,
no API key, queryable by address. I tested it while writing this; a real
record looks like:

> `class: "C"`
> `novdescription: "§ 27-2005 ADM CODE & 309 M/D LAW ABATE THE NUISANCE
> CONSISTING OF HOT WATER EXCEEDING MAXIMUM TEMPERATURE LIMIT IN THE ENTIRE
> APARTMENT LOCATED AT APT 5..."`
> `currentstatus: "VIOLATION CLOSED"`

Read that `novdescription` again. **The city's own data cites the statute.**
It speaks the exact language your citation guard already validates.

So the hook becomes: *type your address, see what the city already knows
about your building.*

- "Your building has 14 open violations. 3 are **Class C — immediately
  hazardous**, including one for heat logged in February that is still
  open."
- "Your landlord has been cited for this exact problem before. That matters
  if you go to Housing Court."
- "Nobody has filed about your apartment. Here's how to, and here's what
  happens after."

Why this is the whole ballgame:

- **ChatGPT cannot do it.** No live data, no address lookup. This is the
  first thing on the site that is *impossible to get elsewhere.*
- **Value before signup.** Right now a tenant hits a login wall before the
  site has proven anything. A lookup that works logged-out inverts that.
- **It makes the chat specific.** Instead of "tell me about your situation,"
  the assistant opens with facts: *"I can see three open heat violations at
  your address. Is that what you're dealing with?"* That's a different
  product.
- **It feeds grounding with facts, not just statutes.** The corpus tells you
  what the law says; the violation record tells you what has actually
  happened at this address. Cite both.
- **It's the reason someone shares the link.** "Look up your building" is a
  sentence a tenant repeats to a neighbour. "An AI chatbot for tenants" is
  not.

**Caveats to build in, not discover later.** HPD data lags — a violation
takes time to appear, and "closed" often means the landlord *certified* a
repair, not that it was done. Address matching is messy (abbreviations,
apartment numbers, buildings with several addresses). Say the "as of" date
on every screen, and let people correct a wrong match.

## 2. Hold the timeline — because housing law is made of deadlines

A chat that forgets is not a case file. The things that hurt tenants are
dates: answer deadlines, inspection windows, how long the landlord has to
fix a Class C (24 hours) versus a Class A.

Give each conversation a small structured spine alongside the messages:
what happened, when, what's next. Then the site can do the one thing a
chatbot never does — **come back to them**: *"You said the inspection was
scheduled for the 12th. Did it happen?"*

Email is enough. No new infrastructure.

This is also what makes the difference between a tool someone uses once and
a tool with a reason to return.

## 3. Turn the conversation into paper

You already asked for the files panel. This is what it's for.

Housing Court runs on paper. A tenant who shows up with a dated log of
complaints, the HPD violation printout, photos and a filled form is in a
materially different position from one who shows up with a story. Give
them: **one button, one PDF** — the timeline, the violation record with its
"as of" date, the filled RA-81, and their own notes.

Also: **more than one form.** RA-81 proves the machinery works. The HP
Action packet and the DHCR rent-overcharge complaint are the ones tenants
actually need next. Each additional form is far cheaper than the first,
because `form_service` already exists.

## 4. Speak their language

NYC tenants are enormously multilingual, and the households most exposed to
bad landlords are often the least served in English. Gemini already
translates well — this is mostly a UI and prompt-plumbing job, not an ML
one.

Two rules that make it real rather than decorative:

- **The whole page, not just the replies.** A translated answer wrapped in
  an English interface is still an English site.
- **Filed documents stay in English, with a translation shown alongside.**
  A court or an agency needs the English form; the tenant needs to
  understand what they're signing. Both, side by side.

This is the single highest-impact accessibility change available, and it's
a genuine differentiator — most tenant tools are English-only.

---

## The friction, separately

These are small and they are why people bounce.

- **The signup wall.** Let the building lookup and the first couple of chat
  turns work logged out, then offer an account *to save this*. You're
  asking for commitment before showing value.
- **Phones.** Assume a tenant standing in an unheated apartment on a phone,
  possibly on data. The layout already survives 390px; now check weight and
  first paint.
- **Call it a case, not a chat.** "New conversation" is chatbot framing.
  "My heat complaint — started Feb 3" is how someone thinks about it, and it
  makes the sidebar navigable after a month.
- **Print.** A print stylesheet is an afternoon and it matters to the person
  walking into 141 Livingston Street.
- **Say what happens next.** Every answer should end somewhere concrete — a
  phone number, a form, a deadline — not trail off into "consult an
  attorney."

---

## What I'd do, in order

1. **HIBP password check.** Half a day, closes your open security item.
2. **Address lookup, logged out.** The hook, and the largest single change
   in what the product *is*.
3. **Ingest the Housing Maintenance Code.** The corpus is built and empty;
   filling it turns the citation guard on and makes the violation
   descriptions resolvable to real law.
4. **Files panel + one-button case packet.** You already asked for this and
   it's the natural home for #2's output.
5. **Timeline and follow-up email.** The retention mechanic.
6. **Spanish first, then the next two languages by demand.**

Items 2 and 3 are the ones that change the answer to "why this site and not
ChatGPT." Everything else compounds on top of them.

## The one thing to decide

**Is this a tenant's tool, or an organizer's tool?**

Everything above assumes an individual tenant with a problem. The same data
supports a very different product: a tenant association or organizer
watching a whole building or a whole landlord's portfolio, with alerts when
new violations post. That's a smaller audience, far stickier, and pulls the
roadmap toward saved searches and notifications rather than chat.

You can't do both well yet. Worth picking before building #2, because the
address lookup looks different in each.
