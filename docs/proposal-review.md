# Review of the two proposals: what works, what doesn't, what to do instead

**Status:** review of `docs/grounded-sources.md` and
`docs/data-architecture-and-privacy.md`. Written adversarially — the job
here is to find the places where those proposals would not have worked,
before any of them is built.

**Headline:** the grounding proposal is directionally right but
**oversold in one specific and important way**, and its version 1 is
heavier than it needs to be. The privacy proposal is sound in principle
but two of its enforcement mechanisms would not have held, and one of its
recommendations (KMS) is premature. Corrected versions of both are at the
end.

---

## Part 1 — In plain English

**The grounding proposal** says: keep the actual text of housing law in
your database, look up the relevant bits before the AI answers, show the
AI only those bits, and then have the app *check the AI's answer* against
what it was shown before the user sees it.

**The privacy proposal** says: instead of writing security rules for the
tables you have today, write them so they attach to the data itself —
so that when you move data between tables next month, the rules move with
it, and a test fails loudly if a new table forgets them.

Both are good instincts. The problems are in the details.

---

## Part 2 — How effective each piece actually is

Effectiveness here means: **what failure does this actually stop?** Not
what it sounds like it stops.

### Grounding

| Piece | Stops | Does NOT stop | Verdict |
|---|---|---|---|
| Storing law in Supabase | The model having no reference text at all | Anything by itself | Necessary, not sufficient |
| Hybrid vector + keyword retrieval | Missing the obviously relevant section | Retrieving a *plausible but wrong* section | Good, ~70–85% on clear questions |
| Prompt directive ("cite only these") | Nothing reliably | Determined hallucination | **Near zero on its own** |
| Citation-marker validation | Invented `[S7]` markers | Wrong claims under valid markers | Moderate |
| **Statute-string scan** | Invented section numbers, made-up case names, fabricated dollar figures | **Misstated content of a real, retrieved section** | **High for its narrow job** |
| Cited / uncited UI split | Users mistaking guesswork for law | Users ignoring the label over time | Good, with a caveat below |

### Privacy

| Piece | Stops | Does NOT stop | Verdict |
|---|---|---|---|
| Existing AES-256-GCM at rest | Leaked DB dump, Supabase console access, RLS misconfiguration | Anyone who can read the app's environment | Already working; real but narrow |
| Column classification registry | New tables silently skipping encryption | Drift, if the registry is a second source of truth | Good idea, wrong implementation (see F11) |
| Repository boundary | Forgotten `encrypt()` calls at new call sites | Nothing else | High value, low cost — the best item in either doc |
| Owner-bound AAD (v2) | A ciphertext copied into another user's row | Anything about data at rest generally | Genuinely solves the "data moves" problem |
| RLS-per-migration test | Tables shipped without row security | Tables created in the dashboard (see F9) | Good idea, wrong implementation |
| Cascade-deletion test | Purge silently going incomplete | Storage bucket objects (see F13) | Good, incomplete |
| Alerting scrub | Plaintext leaking to Slack/Discord | — | Cheap, do it |

---

## Part 3 — Faults found

Ordered by how much damage each would have done.

### F1 — CRITICAL: the validator checks *citations*, not *claims*

The original doc said the validator "is the feature" and that it is what
"actually prevents hallucination." That is **too strong, and the gap is
exactly where the real-world harm lives.**

Worked example, using law I verified while writing this:

> The assistant retrieves NYC Admin Code § 27-2029 correctly. It replies:
> *"Under § 27-2029, your landlord must keep your apartment at 70°F
> overnight."*

Every check in the proposal passes. `§ 27-2029` is in a retrieved chunk.
The marker is valid. And the answer is **wrong** — the overnight minimum
is 62°F, between 10pm and 6am, regardless of outdoor temperature. A
tenant acts on a fabricated number that now carries a citation and a
link.

This is the worse failure mode, not the lesser one, because the citation
makes it *more* convincing. The original proposal would have shipped
claiming to solve hallucination while leaving this open.

**What actually closes it** (in increasing cost):

- **Quote-anchored citation** — a sentence may carry a citation *only* if
  it contains a verbatim quoted span from that chunk. Numbers and
  deadlines must appear inside the quote, not in the paraphrase around
  it. Mechanically checkable, zero extra API cost. **This is the single
  highest-value change to the whole proposal.**
- **Number cross-check** — extract every number-with-unit from the reply
  (`70°F`, `30 days`, `$50`) and require it to appear in a retrieved
  chunk. Cheap, catches the example above outright.
- **Entailment pass** — a second model call asking "is this claim
  supported by this text?" Most thorough, doubles quota use (see F3),
  and is itself a model that can be wrong.

Take the first two. They are deterministic and free.

### F2 — HIGH: retrieval quality is the real ceiling

If retrieval returns the wrong section, everything downstream works
perfectly and produces a confidently cited wrong answer. No amount of
validation fixes bad retrieval — the validator only proves the quote came
from *a* retrieved chunk, not the *right* one.

Tenant questions are colloquial ("there's no heat and my kid is sick");
statutes are titled formally ("Minimum temperature to be maintained").
Embeddings bridge some of that gap, not all of it.

**Mitigation:** a curated mapping from the ~50 most common tenant
questions to their governing sections, consulted *before* the vector
search and allowed to override it. Unglamorous, and it will outperform
the embedding pipeline on the questions that matter most.

### F3 — HIGH: an embedding call per message, on a quota already failing

`ai_service.py` already falls back across models on 429s, and the free
tier's limits are the reason that code exists. The proposal quietly adds
**one more API call per user message** to embed the query, against the
same key.

An entailment pass (F1) would add a third. That is three calls per turn
where there is currently one, on a quota that is already the known cause
of "Sorry, I couldn't get a response right now."

**Mitigations:** cache query embeddings for repeated/similar questions;
skip retrieval entirely on messages classified as chit-chat; or pick an
architecture that needs no per-message embedding at all (see A1/A3).

### F4 — MEDIUM: over-blocking makes the assistant worse

The statute-string scan rejects any law-shaped string not found in the
corpus. Three ways that misfires:

- The user pastes their lease: *"my lease says section 12-4"* — the model
  repeats it back, scan rejects, reply is lost.
- Real law that simply isn't ingested yet: *Local Law 18 of 2022* is
  genuine, relevant, and would be blocked by a corpus containing only
  Title 27 Ch. 2.
- Correct references to bodies and forms (HPD, 311, RA-81) that look
  statute-adjacent to a regex.

**Fix:** exclude spans quoted from the user's own message; treat
un-ingested-but-real law as *downgrade to uncited*, not *reject*; and
start with logging-only enforcement for two weeks to measure the false
rejection rate before letting the guard block anything.

### F5 — MEDIUM: section-sized chunks don't fit the embedding model

`gemini-embedding-001` caps input at **2,048 tokens**. Housing Maintenance
Code sections range from one sentence to several pages. "Chunk by
section" as written will silently truncate the long ones — and long
sections are disproportionately the important ones.

**Fix:** sub-chunk long sections with overlap, but make every sub-chunk
carry its parent section as the citable unit, so a quote from paragraph
(c) still cites `§ 27-2029` rather than `§ 27-2029 chunk 3`.

### F6 — MEDIUM: a citation is an assertion of authority

Worth thinking about alongside the disclaimer work just shipped. Today
the app says "general information, not legal advice." Adding "Based on
NYC Admin Code § 27-2029" attaches an authority claim to specific
statements. If the claim is wrong (F1), that is arguably a **worse**
position than having said nothing — the user's reliance was actively
invited.

This is not a reason to skip citations. It is a reason to:

- keep the standing disclaimer on cited replies, not suppress it there;
- word the citation as *"Related law: § 27-2029"* or *"See § 27-2029"*
  rather than *"Under § 27-2029, you must…"*, i.e. point at the source
  rather than asserting its content; and
- never let a citation chip appear without the link that lets the user
  check it.

### F7 — LOW: "as of" dates may not be obtainable

The schema has `effective_date`, but Open Legislation and the Admin Code
publishers do not reliably expose when a section last changed. What you
can always know is when *you fetched it*. Label it honestly — "retrieved
2026-09-20" — rather than implying you know the law's vintage.

### F8 — CORRECTION: wrong embedding model named

The original doc specified `text-embedding-004`. The current model is
**`gemini-embedding-001`** (flexible output dimensions; 768 / 1536 / 3072
recommended; 2,048-token input cap). 768 remains the right choice here.

### F9 — HIGH: the migration-parsing test would not have worked

The proposal enforces classification, RLS and cascade rules by parsing
`supabase/migrations/*.sql`. That fails in the way that matters:

- **Schema changes made in the Supabase dashboard never appear in those
  files.** The test passes while the real database drifts. Given how this
  project has been built so far, that is not a hypothetical.
- Regex-parsing SQL breaks on `alter table add column`, quoted
  identifiers, generated columns and `create table as`.

**Fix — and it is strictly better:** run the checks against
`information_schema` and `pg_policies` on the **live database** in CI. The
live schema is authoritative, dashboard edits included. Three queries
replace the whole parser:

- every column in the registry and no unclassified column outside it;
- `rowsecurity = true` on every table holding user data;
- a `ON DELETE CASCADE` path from every user-data table to `auth.users`.

### F10 — HIGH: a lazy-only rewrap never finishes, and you can't tell

The proposal leans on `needs_rewrap()` upgrading rows as they are read —
"no migration window, no batch job." Two problems:

- Rows in conversations nobody reopens stay on v1 **forever**. You can
  never retire v1, which defeats the point of having versions.
- Worse, specific to owner-bound AAD (§3 of the privacy doc): a v2 rewrap
  needs the `user_id` at write time. Any code path that fetches rows
  without selecting the owner column **cannot** rewrap, and will silently
  skip — permanently, with no error.

**Fix:** keep lazy rewrap, but add (a) a `SELECT count(*) WHERE content
LIKE 'enc:v1:%'` metric you can actually look at, and (b) a pg_cron sweep
that finishes the tail. An unfinishable background migration is worse
than an honest batch job, because it looks done.

### F11 — MEDIUM: the registry is a second source of truth

A Python dict listing every column will drift from the schema the first
time someone adds a column without touching it — and the whole point was
to survive exactly that person.

**Better:** store the classification *in the database*, as column
comments:

```sql
comment on column messages.content is 'class=USER_CONTENT';
```

It cannot drift from the schema because it lives on the schema, it
survives dashboard edits, and the CI check in F9 reads it directly. The
app loads it once at boot.

### F12 — MEDIUM: the threat model doesn't match the likely breach

Worth saying plainly, because it changes where effort should go.

Application-held encryption defends against a stolen database. For a
student project on Render's free tier, the more probable incidents are:
a key committed to a public repo, a leaked or phished GitHub/Render/
Supabase login, or a vulnerable dependency. **Every one of those yields
the encryption key along with the data**, because the key lives in the
app's environment.

That does not make the encryption work wasted — it genuinely closes the
dump/console/RLS-misconfiguration path, and `docs/data-encryption.md`
already describes the boundary honestly. But if the goal is *reducing the
chance a tenant's housing situation becomes public*, the higher-return
items are unglamorous:

- 2FA on GitHub, Supabase and Render, and a check that no key has ever
  been committed;
- Dependabot or `pip-audit` in CI;
- the RLS correctness tests from F9;
- not logging user content (F15).

Do those before KMS, before AAD, before anything else in the privacy doc.

### F13 — MEDIUM: Storage objects escape the account purge

The pg_cron purge works through `ON DELETE CASCADE`. Objects in a
Supabase Storage bucket have no foreign key to `auth.users`. The moment
the files panel ships, deleting an account will leave the user's
generated PDFs and Markdown in the bucket — silently, and contrary to
what the deletion UI promises.

**Fix:** a `user_files` table that owns the bucket path and cascades, plus
a purge step that deletes the objects. This must land *with* the files
panel, not after.

### F14 — LOW: KMS is premature

The original doc pointed at AWS/GCP KMS as "the real upgrade path." Re-
examined: it costs roughly a dollar per key per month plus per-request
fees, adds a network round trip to decryption, and interacts badly with
Render free-tier cold starts unless data keys are cached in memory —
which reintroduces the exact exposure KMS was meant to remove.

**Revised:** keep keys in environment variables. Rotate manually using
the mechanism that already exists. Revisit if this ever handles other
people's users at scale.

### F15 — LOW: the alerting leak was overstated

The original review called the `ALERT_WEBHOOK_URL` path "the most
realistic plaintext-egress path in the current codebase." More precisely:
it is **latent** — alerting is a no-op unless the webhook URL is
configured. It is one config change plus one careless `logger.exception`
away from being live, which is still worth closing, but it is not
currently leaking.

Also, a regex scrub is the weaker fix. **Better:** structured logging
with an explicit allow-list of loggable fields, so user content cannot be
passed by accident rather than being caught after the fact.

### F16 — CONTEXT: the SHIELD Act probably applies to you

Not a fault in the proposal, but missing from it, and you should know.

New York's SHIELD Act applies to *any* person or business holding
"private information" of New York residents, regardless of size or where
the business operates. Its definition of private information includes
**an email address together with a password or security question** — i.e.
exactly what an account system holds. It requires reasonable
administrative, physical and technical safeguards, explicitly scaled to
the size and complexity of the business, and it requires breach
notification.

Two practical consequences:

- The existing encryption, RLS, lockout and deletion work maps directly
  onto "reasonable technical safeguards." Keeping `docs/data-encryption.md`
  current is not just documentation; it is the evidence that a program
  exists.
- The most tightly regulated data in the app — credentials — is the part
  you deliberately **don't** hold, because Supabase Auth owns it. That is
  a good position. Keep it: never copy passwords or tokens into your own
  tables.

Chat content about someone's housing situation is sensitive but falls
outside SHIELD's narrow definition. Treat it carefully because it is the
right thing to do, not because a statute forces the specific measures.

---

## Part 4 — Alternatives, including ones better than the proposal

### A1 — Put the corpus in the prompt instead of retrieving it

The Housing Maintenance Code's heat, repairs and habitability articles
are small — low tens of thousands of tokens. Gemini's context window
holds them comfortably, and context caching makes resending them cheap.

- **Eliminates:** the vector extension, the ingest pipeline, embedding
  calls per message (F3), retrieval-miss failures (F2).
- **Keeps:** the quote-anchored validator, which still works unchanged.
- **Costs:** tokens per call; doesn't scale past roughly one code chapter.

**This is a better version 1 than the proposal was.** It tests the whole
citation idea in days rather than weeks, and if it works, retrieval
becomes a scaling optimisation you add when the corpus outgrows the
window — not a prerequisite.

### A2 — Curated question→section routing

A hand-written table of the top ~50 tenant questions mapped to governing
sections, consulted before anything statistical. Beats embeddings on
common questions, is inspectable, and is trivially correctable when
wrong. Pairs with anything else here.

### A3 — Full-text search only, no pgvector

Postgres FTS with a synonym dictionary ("heat" → Article 8, "lockout" →
Title 26 Ch. 5). No extension, no embedding API calls, no vector index.
Weaker on paraphrase, strong on the keyword-ish questions tenants
actually type. A reasonable permanent answer at this corpus size.

### A4 — Quote-anchored citations (strongly recommended, adopt regardless)

Covered in F1. A citation is valid only if the sentence carries a
verbatim span from the cited chunk, with numbers inside the quote. Turns
the validator from "the citation exists" into "the claim is copied from
the source," which is the guarantee that was actually wanted.

### A5 — Classification as column comments, not a Python registry

Covered in F11. Cannot drift from the schema, survives dashboard edits.

### A6 — CI checks against `information_schema`, not against SQL files

Covered in F9. Authoritative, dashboard-proof, and less code.

### A7 — Skip KMS; keep environment-variable keys

Covered in F14.

### A8 — Allow-list structured logging, not a regex scrub

Covered in F15.

---

## Part 5 — Revised recommendation

**Grounding — a leaner version 1:**

1. Ingest one corpus: Housing Maintenance Code, Title 27 Ch. 2. Store
   text, official URL and retrieval date. No embeddings yet.
2. Build the guard first, tested against deliberately fabricated replies:
   quote-anchoring, number cross-check, statute-string scan, with
   user-quoted spans excluded.
3. Run the guard in **log-only mode** for two weeks. Measure the false
   rejection rate before it blocks anything.
4. Put the corpus in the prompt (A1) with a curated routing table (A2)
   picking which articles to include.
5. UI: *"See § 27-2029"* with a link, or the uncited label. Standing
   disclaimer stays on both.
6. Add pgvector only when the corpus outgrows the context window.

**Privacy — reordered by actual return:**

1. 2FA everywhere; verify no key was ever committed; `pip-audit` in CI.
   (F12 — highest return, lowest effort, currently missing.)
2. Allow-list logging so user content cannot reach the alert webhook.
   (F15/A8.)
3. The repository boundary, so encryption can't be forgotten at a new
   call site. (The best item in the original doc, unchanged.)
4. Column comments for classification + three `information_schema` CI
   checks for classification, RLS and cascade coverage. (A5, A6.)
5. `user_files` with cascade and a purge step — shipped *with* the files
   panel, not after. (F13.)
6. Owner-bound AAD as envelope v2, **plus** the v1 row counter and the
   pg_cron sweep. (F10.)
7. KMS: not now. (F14.)

**What changed most:** the citation guard got stronger and the retrieval
pipeline got deferred; the privacy work got reordered so the cheap,
boring, high-return items come before the cryptographically interesting
ones.
