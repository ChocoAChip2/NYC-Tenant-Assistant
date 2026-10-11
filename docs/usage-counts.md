# Usage counts

Added 2026-10-11. Code: `usage_metrics.py` (what may be counted, buffering),
`usage_routes.py` (where it is counted, the click endpoint, `/privacy`),
migration `*_usage_daily_counts.sql`, and two files in `supabase/checks/`.
Public wording: `branding.PRIVACY_*`.

## What is stored

One row per **(day, event, one value from a fixed list)** with a count.
Nothing else exists to store: the table has four columns (`day`, `event`,
`dim`, `n`).

- **No identifiers.** No user id, session or visitor id, IP address, hash
  of any of those, cookie, localStorage id or fingerprint.
- **No free text and no personal data.** No chat content or topics, titles,
  names, emails, streets, addresses, apartment numbers, ZIP codes, cross
  streets or URLs.
- **One dimension per counter, per day.** Never a cross-tab such as borough
  by outcome by hour: combinations of rare values are how aggregate data
  re-identifies people.
- **Chat is counted only as "a message was sent"**, plus whether the reply
  carried citations, which guard checks fired (by kind) and errors.

## How that is enforced, twice

1. `usage_metrics.record(event, dim)` has two parameters and drops the call
   unless both are on `EVENTS` (or, for `law_view`, the value is shaped like
   a section number of public law). There is no parameter that could carry
   an id or text.
2. The database function `record_usage(jsonb)` checks the same allowlist
   again (`usage_allowed`, generated from `EVENTS`; a test keeps the two
   equal), caps lengths, caps one row at 1000, takes at most 200 rows, and
   stamps the day itself. `anon` and `authenticated` can execute it and can
   do nothing else to either table.

The app has no service-role key, so it cannot read the counts back. They
are read in the SQL editor with
`supabase/checks/20261011_usage_counts_report.sql`, whose queries hide any
total under 5.

## Where counting happens

One `after_request` hook on the blueprint (`usage_routes.count_request`).
It reads the **route name** and the **response status**, never the URL.
That is why almost no route had to change, and why a new route is simply
not counted until it is added to `usage_metrics.PAGES`.

| Event | Values | Taken from |
|---|---|---|
| `page_view` | page names | route name, on a 200 HTML GET |
| `view_device`, `view_language`, `view_referrer`, `view_signed_in` | fixed lists | request headers, reduced to a category and then discarded |
| `area_search`, `building_lookup` | found / no_match or not_found / invalid / error | response status |
| `area_borough`, `area_cross_used`, `area_zip_used` | borough; yes / no | validated borough; whether the field was filled, never its value |
| `law_view` | section number | route argument, on a 200 |
| `chat` | message_sent, reply_with_citations, conversation_created / archived / deleted | route name, status, whether the reply listed sources |
| `guard_violation` | the guard's kinds | `routes._ground_reply` |
| `ai_error` | unavailable / failed | status of the message route |
| `ra81` | draft_started / reviewed / downloaded | `chat_message`, the review page |
| `signup`, `account_complete`, `login` | steps | route name and status |
| `chip_click` | chip ids | a hidden field on chip forms, or `?chip=` on the two link chips |
| `resource_click` | ids made from the fixed resource lists | `POST /usage/click` |
| `error_route`, `error_status` | page name; status code | any response of 400 or more |

Drop-off is read from step totals per day, never from following one
visitor: that would need an identifier.

**Not counted, though the brief listed it:** an "ambiguous" street-search
outcome (a street that spans districts). The status code doesn't show it,
and counting it means touching the area route, which another open change
also edits. Easy to add later.

**Approximate by design:** archive and delete are counted when the request
is accepted, including the rare one that then fails; "account details
saved" likewise.

## The click endpoint

Outbound links on Resources go straight to the agency, so the server never
sees them. `POST /usage/click` takes one id from the fixed list and answers
204 whatever happens.

It is the one POST without a CSRF token, on purpose: a token needs a
session cookie, and Resources is read by people who are not signed in and
who get **no cookie from us at all**. Instead it accepts only requests the
browser marks as coming from our own pages (`Sec-Fetch-Site: same-origin`,
or a matching `Origin`), is rate limited, and can only add 1 to a counter
on the list. `app.py` exempts just that view.

## Buffering

Counts add up in memory and are written in one call after 50 events or 60
seconds, on a background thread, so no page waits on the database. A
restart loses what had not been written. If the write fails (including
before the migration is applied) the counts are dropped and nothing is
retried for five minutes.

## Opting out

A browser that sends Global Privacy Control (`Sec-GPC: 1`) is not counted
at all, on any event. There is no banner (owner's choice, 2026-10-11).

## Retention

A daily pg_cron job (`purge-usage-daily`) deletes rows older than 12
months.

## What this does not fix

- **Anyone with the public API key can add to a counter on the list.** They
  cannot write anything else or read anything. Treat the counts as an
  indication, not an audit record.
- **Addresses still travel in URLs** (`/building?address=...`). The counts
  never see them, but whatever the host keeps in its own request logs is
  outside this change and was not checked.
- **The error webhook still forwards log text unscrubbed** and the citation
  guard still logs fragments of replies. Both were listed as separate
  cleanups.
- The privacy page wording has not been reviewed by a lawyer.

## Before it works in production

1. Apply the migration (owner's go-ahead; test it first in a rolled-back
   transaction), then rename the file to the version the database records.
2. Run `supabase/checks/20261011_usage_counts_checks.sql`; it rolls back
   and should end with "all usage checks passed". Run the advisors.
3. Until then the app runs normally and simply records nothing.
