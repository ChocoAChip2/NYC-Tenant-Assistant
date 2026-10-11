# Case workspace: "Your data" and reviewed RA-81 drafts

Added 2026-10-06 (Codex review pass, `docs/review-2026-10-06.md`); merged
2026-10-08 together with the street lookup. Reworked 2026-10-11: the form
above the transcript ("Your situation & reply preferences") became a "Your
data" button in the composer, filled in by the assistant.

## "Your data" (`templates/_your_data.html`, `case_context.py`, `case_routes.py`)

**Where it is.** The composer has a toolbar row under the textarea with two
controls on the right: the "Your data" pill and Send. The pill has an
ID-card icon and a tooltip (CSS only, shown on hover and keyboard focus,
tied to the button with `aria-describedby`). Clicking it opens a popover
above the composer; at 700 px and below it is a bottom sheet with a
backdrop. Below 340 px the label is visually hidden and the icon stays.

**Why the popover is outside the chat form.** It contains its own form
(Edit details), and forms cannot nest: Enter in one of its fields would
send the chat message. The button is inside `#chat-form`, the popover is a
sibling inside `.composer`.

**What is in it.**
- Summary: borough, apartment status, issue, when it started, actions
  already taken, goal. One line each (ellipsis; full text in the title and
  in Edit details). Blank fields are hidden, and so is a choice still at
  its default unless the tenant set it.
- Reply language and answer length: each row opens a list of choices;
  picking one saves at once and returns to the main view.
- Edit details (the six facts, inline) and Clear details (confirmed inside
  the popover; reply settings are kept).
- RA-81 drafts for this chat, only when one exists.
- "Manage all your data" links to the section of the same name in Settings
  (`#your-data`), which handles export and deletion.

**Keyboard.** Enter/Space on the button opens it and focus moves to the
first row. Up/Down/Home/End move between rows (buttons and links; not
inside a text field or select). Escape goes back from a sub-view, or closes
the popover and returns focus to the button. Clicking outside or tabbing
out closes it.

**Who fills it in.** The assistant, from the tenant's own messages only:
- After a reply arrives the page calls
  `POST /conversations/<id>/situation/refresh`. The reply never waits on
  it, and a failure shows nothing in chat.
- The route sends the model the current summary and the tenant's messages
  it has not read yet (at most the last 8, 2,000 characters each). The
  assistant's replies are never sent, so a suggestion ("you could call
  311") cannot become an action taken.
- The answer is data: `case_context.parse_extracted()` must find a JSON
  object, and `apply_extracted()` keeps only the six fact fields, with
  values inside `OPTIONS` / `TEXT_LIMITS`. Anything else is dropped. An
  update can add or replace a fact, never blank one. Reply language and
  answer length are never guessed ("Match my message" already follows the
  language the tenant writes in).
- A fact the tenant edits is theirs: the field is recorded in `tenant_set`,
  left out of what the model sees, and skipped by later updates.
- Clear details blanks the facts, empties `tenant_set`, and marks every
  existing message as read, so old messages do not refill it.
- Limits: the refresh route has the chat route's limit (20 a minute, 300 a
  day) and only calls the model when there is an unread tenant message, so
  it can never make more model calls than the tenant sent messages. If the
  model fails or answers with something unusable, nothing is saved and the
  same messages are read next time.

**Storage.** No schema change. `conversations.case_context` holds one
encrypted JSON value: the validated fields plus `_tenant_set` and `_seen`
(how many tenant messages have been read). `case_revision` is still the
optimistic lock; a stale save gets 409 and the page reloads and retries
once. Details saved before this change load unchanged.

**Sending is never blocked.** The old rule (no sending until the panel had
loaded and had no unsaved edits) is gone. The send path still reads the
saved details on the server; if that read fails the message gets a 503, as
before.

The saved JSON is added to the model's history as untrusted tenant data
(`case_context.instructions()`), never as instructions. Short follow-up
questions borrow the saved issue for law retrieval
(`case_context.retrieval_query()`); new topics do not.

**CSS notes.** `chat.html` has a page-wide `textarea` rule (borderless,
transparent, for the composer); the popover's fields override it with
`.yd-pop .yd-fields textarea`. The composer textarea is `flex: 0 0 auto`
because in a column flexbox `flex: 1` would ignore the height the
auto-grow script sets. Open/close uses a class with `visibility` and
`opacity`, not `hidden`, so it can fade; the sub-views do use `hidden` and
rely on the page's `[hidden] { display: none !important; }`.

## RA-81 drafts (`form_review.py`, `templates/form_review.html`, `case_routes.py`)

- A complete RA-81 intake from chat no longer downloads a PDF. It saves an
  encrypted, immutable row in `form_drafts` and replies with a link to
  `/conversations/<id>/forms/<draft_id>`.
- That page shows every field for editing. Download requires: a confirmed
  rent-regulated status, an individual-apartment service issue (heat/hot
  water → HHW-1, building-wide → RA-84), the confirmation box, and all
  fields DHCR needs. Edits are saved as a new draft before download.
- JSON naming any form other than RA-81 is refused in chat.

## Storage and access

Migration `supabase/migrations/20261008014633_case_context_and_form_drafts.sql`:
two columns on `conversations`, and `form_drafts` with RLS so a tenant can
only read, insert or delete their own drafts on their own conversations.
No update policy: drafts are immutable. Both are included in the
chat-history export and removed with the conversation.

## Local QA without external services

`venv/bin/python -m tools.review_server` runs the app on 127.0.0.1:5057
with in-memory data (`tests/review_support.py`). Never imported by the app.
Its stand-in assistant fills "Your data" from keywords (a borough name,
"heat" or "radiator", "rent stabilized", "called 311").
