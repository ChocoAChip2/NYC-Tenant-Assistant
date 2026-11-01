# Summarized PDF report of a chat (proposal)

**Status:** proposal from the owner, 2026-11-01. Priority medium. Nothing
is built yet. Scope beyond the four sections below is still open.

## What it is

After a chat, the tenant can get a printable summary of it and use it
offline. Once they have the PDF they should not need the site to act on it.

## What the PDF holds (owner's list)

1. **Summary of the issues** discussed in that conversation.
2. **Next steps**: an action plan the tenant can follow.
3. **Where it isn't sure**: each point the assistant can't stand behind
   is marked, with who to ask instead (HPD, 311, a tenant attorney or
   legal aid, Housing Court help centers).
4. **Sources**: links a tenant can open to check every claim (the law
   sections, HPD Online and NYC Open Data for building records).

Design rule: never state anything with more certainty than its source
gives. When a matter needs a professional, the PDF says so plainly.

## Delivery (owner's list)

- **Email** a copy to the tenant.
- **Download** on the site at the moment it is made (optional).

## How it fits what already exists

- Sources come only from what the app actually retrieved or looked up:
  the passages `retrieval_service` showed the model, and building links
  from `building_service` (HPD Online, NYC Open Data). The model never
  supplies a URL. `citation_guard` already checks replies against
  retrieved passages. The summary must go through the same check.
  Anything it drops becomes an "ask a professional" item, not a guess.
- The "where it isn't sure" section is partly fixed text (who to call for
  what, from `branding.HELP_RESOURCES` and `resources.py`) and partly the model's own flags. It is never
  empty: the general "not legal advice" line (`branding.SHORT_DISCLAIMER`) always
  appears.
- PDF is made on request, sent, and not kept. No summary text in logs
  (same rule as chat replies).

## Proposed build order

1. Download first. A "Save summary (PDF)" action in the conversation menu.
   One model call with a fixed JSON shape (issues, next steps, unsure
   points, source ids), checked in code, rendered server-side to PDF.
   Translated pages get a translated PDF, with the same English-reference
   note the site uses.
2. Email second, once sending is set up (see decisions).

## Decisions needed from the owner

- **Email sender.** The app sends no email of its own today (Supabase
  sends sign-in mail, and custom SMTP there is still pending). Emailing a
  PDF needs a transactional email service, a verified sending domain and
  a key in Render. Which service and domain?
- **Email and privacy.** The PDF holds what the tenant told the
  assistant. Email is less private than the site. Send only after the
  tenant asks each time? Attach the PDF, or send a short-lived download
  link instead? The privacy page will need a line either way.
- **PDF library.** A server-side PDF library is a new dependency (the
  app only has `pypdf`, which fills forms but doesn't lay out text).
  Alternative: a print-styled page the browser saves as PDF, no new
  dependency, but no email attachment.
- **More sections?** Pinned for later by the owner.
