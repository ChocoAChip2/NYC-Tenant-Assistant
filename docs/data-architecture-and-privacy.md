# Structuring security so the data can keep moving (proposal)

**Status:** proposal. `docs/data-encryption.md` describes what is already
built; this describes how to keep it true as the schema grows.

The constraint that shaped this: *the data will be moved around as the
site develops.* Two tables today become eight — a files panel, per-user
memory, retention policies, a legal corpus. Most security designs fail at
exactly that point, not because anyone disables them, but because a new
table lands and nobody remembers which rules were supposed to apply to
it.

So the design goal is not "more encryption". It is **making the rules
attach to the data rather than to the place the data happens to live.**

## 1. Classify every column, in code, once

A single registry module — the way `branding.py` holds the one string
with legal weight — naming each column and its class:

| Class | Meaning | Treatment |
|---|---|---|
| `PUBLIC` | Law, help resources | Plaintext, indexed, world-readable |
| `USER_CONTENT` | Messages, titles, uploaded files, notes | Encrypted, RLS, cascade-deleted |
| `IDENTITY` | Email, auth tokens | Owned by Supabase Auth — never copied into our tables |
| `OPERATIONAL` | Timestamps, sizes, mime types, counts | Plaintext (needed for indexes and quotas) |
| `DERIVED` | Summaries, extracted form fields, embeddings of user text | Encrypted — a summary of a tenant's situation is still the tenant's situation |

Then a test that reads `supabase/migrations/*.sql`, finds every
`create table` and every column, and **fails if any column is not
classified.** That single test is what makes the policy survive the next
twelve features. A developer adding a table has to make a decision rather
than inherit a default.

`DERIVED` is the class people forget. An embedding of a message is not
the message, but it is close enough to reconstruct its substance — it
belongs on the encrypted side, which incidentally means user-content
vector search has to be client-side or not at all. Worth deciding
deliberately rather than discovering later.

## 2. Encrypt at one boundary, not at eight call sites

Today `crypto_service.encrypt()` is called by hand in `supabase_service.py`
— roughly eight sites. That was right for two tables. It is the wrong
shape for eight, because the failure mode is silent: someone adds an
insert, forgets the call, and the column stores plaintext that looks fine
in every test.

Move to a thin repository layer that consults the registry and
encrypts/decrypts by field name on the way in and out. One place to
audit, and a test can then assert that *every* `USER_CONTENT` column
round-trips through it.

## 3. Bind ciphertext to its owner, not to its address

This is the direct answer to "the data can be moved around".

The envelope is already versioned: `enc:v1:<key id>:<payload>`, with an
algorithm registry and a rewrap-on-read path. The next version should add
authenticated associated data (AAD) — a string bound into the
authentication tag, so a ciphertext only decrypts in the context it was
sealed in.

The question is what to bind, and it matters more than it looks:

- Bind to **table name + row id** and you get the strongest guarantee —
  and every legitimate migration that moves a row becomes a
  decrypt-and-re-encrypt operation with a key in hand. That fights the
  stated requirement.
- Bind to **`user_id` + logical field name** (`message.content`,
  `file.body`) and rows can move between tables, be renumbered, be
  re-parented, and migrate wholesale — while a ciphertext still cannot be
  copied from one user's row into another user's row and read back.

Take the second. Bind to what must never change (who owns it, what it
means), not to where it currently sits. Under the existing envelope this
is `v2` in the algorithm registry, and `needs_rewrap()` upgrades rows
silently as they are read — no migration window, no batch job.

## 4. RLS is the boundary that actually says "only this user"

Encryption defends against a database dump. RLS defends against the app
serving the wrong row, which is the far more likely failure. As tables
multiply, the rule has to be mechanical:

> Every migration that creates a table must, in the same file, enable RLS,
> add its policies, and `revoke all ... from anon`.

Enforceable by the same migration-reading test as § 1. A table shipped
without RLS on a Friday is readable by every authenticated user until
someone notices.

## 5. Deletion has to follow the data

There is already an account-deletion flow: a request row, a 30-day
cancellable grace period, and a `SECURITY DEFINER` pg_cron purge that
leans on `ON DELETE CASCADE`. The moment a new table holds user data and
*isn't* hung off `auth.users` (directly or through `conversations`), the
purge quietly stops being complete, and it fails silently — nobody
notices data that wasn't deleted.

Add a test that queries `information_schema` for every table classified
`USER_CONTENT` or `DERIVED` and asserts a cascade path to `auth.users`.
The files panel and per-user memory both create exactly this kind of
table, so this is worth having in place before they land.

## 6. Decide the searchability trade before building the files panel

Encrypted columns cannot be indexed. That is not a limitation to work
around, it is a choice to make explicitly, per column:

- **File contents and titles** → `USER_CONTENT`, encrypted, no
  server-side search. Search the decrypted list client-side; a single
  user's file list is small enough that this is genuinely fine.
- **Size, mime type, created_at, conversation_id** → `OPERATIONAL`,
  plaintext, indexed. Enough for sorting, quotas and the panel UI.

If full-text search over a user's own documents becomes a requirement
later, that is a real architectural change (encrypted search indexes,
or a deliberate declassification), not a small feature. Better to know
that now.

## 7. Supabase Storage is outside the column encryption

The generated Markdown and filled PDFs will not live in a column. Objects
in a Storage bucket are encrypted by Supabase at rest under Supabase's
keys, which is a weaker guarantee than the app-held key protecting the
message bodies — an inconsistency users would not expect.

Encrypt file bytes app-side with the same envelope before upload, keep
the bucket private, and serve through short-lived signed URLs. The
envelope's version prefix works on bytes exactly as it does on text.

## 8. Keys: where they go next

Keys currently live in Render environment variables. That is appropriate
for now and the envelope makes it replaceable later. Two notes:

- **Supabase Vault is installed on this project, and is the wrong home
  for these keys.** Storing the decryption key in the same database as
  the ciphertext removes the exact boundary the encryption exists to
  create. It is the right tool for other secrets, not this one.
- The real upgrade path is a KMS (AWS or GCP) with envelope encryption:
  a per-user data key wrapped by a master key the app never sees, so
  rotation and revocation are KMS operations. The existing `key_id`
  field already accommodates this — a `kms:` prefixed id routes to a
  different unwrap path, old rows keep opening under their old ids.

## 9. The leak that is easiest to ship by accident

`alerting.py` posts every `ERROR` log record to `ALERT_WEBHOOK_URL` —
Slack or Discord. One `logger.exception("failed on message %s", content)`
added in a hurry and plaintext tenant messages are flowing to a third
party, outside every boundary described above, permanently, in someone's
chat history.

Add a scrub in `alerting.py` (drop anything matching the ciphertext
envelope prefix, truncate long free text, redact email addresses), and a
test that a log record containing a known message body does not reach the
webhook payload intact. This is cheap and it closes the most realistic
plaintext-egress path in the current codebase.

## 10. Retention, while the subject is open

The earlier request for an incognito / temporary chat mode with 30-day
auto-deletion fits naturally here: a `retention_policy` column on
`conversations` (`standard` | `ephemeral`), enforced by the same pg_cron
job that already runs the account purge. Retention is a property of the
data, which means it survives the data moving — the same principle as § 3.

## Suggested order

1. § 9 (the webhook scrub) — smallest, closes a live path.
2. § 1 + § 4 + § 5 (the registry and the three migration tests) — no
   behaviour change, and every later feature inherits the guarantees.
3. § 2 (repository boundary) — before the next table, not after.
4. § 6 decisions, then the files panel.
5. § 3 (`v2` envelope with owner-bound AAD) — independent, any time.
6. § 8 (KMS) — when there is a reason beyond tidiness.
