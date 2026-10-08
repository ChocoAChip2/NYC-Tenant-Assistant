# Case workspace: "Your situation" and reviewed RA-81 drafts

Added 2026-10-06 (Codex review pass, `docs/review-2026-10-06.md`); merged
2026-10-08 together with the street lookup.

## "Your situation" panel (`templates/_situation.html`, `case_context.py`)

- One per conversation, shown above the transcript. The tenant fills it in;
  nothing is inferred from model text.
- Fields and limits live in `case_context.OPTIONS` / `TEXT_LIMITS`.
  `case_context.validate()` rejects anything outside them (400).
- Stored encrypted in `conversations.case_context`; `case_revision` is an
  optimistic lock. A save from a stale tab gets 409 and must reload.
- **Sending is blocked until the panel has loaded and has no unsaved
  edits.** If `GET /conversations/<id>/situation` fails, chat cannot send —
  so the migration below must be live before this code deploys.
- The saved JSON is added to the model's history as untrusted tenant data
  (`case_context.instructions()`), never as instructions. Short follow-up
  questions borrow the saved issue for law retrieval
  (`case_context.retrieval_query()`); new topics do not.

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
