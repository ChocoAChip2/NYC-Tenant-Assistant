# Feedback question in the chat (`feedback.py`)

Owner, 2026-11-08: ask for feedback at a good moment, when a longer chat is
coming to an end, not in the middle of someone's problem.

## When it appears

Decided on the server after each reply (`feedback.offer`, returned as
`feedback` in the `/chat/message` JSON); the page only times it.

- Never before the tenant has sent 3 messages in the conversation.
- Said goodbye: a short sign-off with no question in it ("thanks, that's
  all", "got it", "gracias", "谢谢") after 3+ messages shows it 1.5 s
  after the reply.
- Gone quiet: after 5+ messages, 90 s with no typing after a reply, with
  the tab in view. Typing or sending cancels the timer.
- Not after an RA-81 draft hand-off, not while a reply is pending, once
  per page at most.
- After an answer the `feedback_asked` cookie rests the question: 30 days
  after sending, 3 days after "Not now", a year after "Don't ask again".

## The card

"Was this chat helpful?" Yes / Somewhat / No, then an optional comment
(1,000 characters) and Send; "Not now" and "Don't ask again" underneath.
Non-modal, above the message box, announced politely to screen readers.

## What is kept

`submit_feedback()` (`supabase/migrations/*_feedback.sql`), signed-in users
only: rating 1-3, comment encrypted by the app (dropped if encryption is
off), trigger (`closing` or `idle`), page language and the day. No account,
conversation or time of day. Deleted after 12 months by pg_cron. Read
from the SQL editor; decrypt comments with the data key as for the launch
list. POST /feedback is limited to 10 a day.
