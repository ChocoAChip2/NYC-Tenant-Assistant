# Account requirements: asking existing accounts for new information

**Rule for every feature:** if a feature needs information that accounts
made before it won't have, it must declare that information as a
requirement in `account_requirements.py`. Don't assume every account has
gone through the current sign-up form. Existing accounts are then asked
for it automatically, and new accounts that give it at sign-up are never
asked.

## Why

Sign-up started asking for a name and date of birth on 2026-09-30. Every
account made before that had neither, and nothing ever asked them. Chat
greeted them by their email handle, and their age was never checked. Any
future field (a borough, a preferred language, a phone number for
reminders) would repeat that gap. This is the general fix.

## How it works

1. **At login**, the app reads the account's `user_metadata` from the
   sign-in response and works out which requirements aren't met. It
   stores that list in the session, together with `REQUIREMENTS_VERSION`.
2. **Sessions that were already signed in** when a requirement was added
   carry an old version. The first gated page they open (currently
   `/chat`) re-reads the account once and records what's missing. Nobody
   has to log out for a new requirement to take effect.
3. If anything is pending, the tenant is sent to **`/account/complete`**,
   one page that shows each pending requirement's fields.
   - **Save and continue** writes them in a single metadata update
     through the tenant's own session. No service-role key is involved.
   - The tenant then continues to where they were going.
4. **Settings → Your name** edits the same data at any time, and marks
   the requirement met for the session.

### Never a dead end

- **Can't be collected right now:** a requirement that can't be
  collected (the profile, with no encryption key configured) is not
  asked.
- **Save fails:** the tenant carries on, and is asked again at the next
  login.
- **Account can't be read** (auth down): nothing is gated, and the check
  runs again 10 minutes later (`RETRY_UNKNOWN_SECONDS`), not on every
  page.
- **Optional requirements** (`required=False`) offer **Remind me next
  time**. That skips them for the session; the next login asks again.
- **Required requirements** (like the age check) don't offer a skip.
- **Always reachable:** public pages, Settings (export and account
  deletion), logout and `/account/complete` are never gated. Add an
  endpoint to `GATED_ENDPOINTS` only if the feature behind it truly needs
  the information.

## Adding a requirement

1. In `account_requirements.py`, define a `Requirement`:
   - `key`, `version`, `title`, and `why`. `why` is one short, plain
     sentence saying why you're asking; it's shown on the page. Optional
     `why_detail` goes behind an "i" button next to it.
   - `template`: `templates/_requirement_<key>.html`. It holds just the
     fields, uses the `.field` / `.name-row` / `.privacy-note` styles of
     `account_complete.html`, and has no HTML/CSS/JS comments.
   - `required`: `True` only if the product must not be used without it.
   - `is_met(metadata)`: does this account already have it? This must
     never raise; a check that raises is treated as met.
   - `can_collect()`: can it be stored right now?
   - `collect(form, today)`: validate and return `Collected(metadata,
     session_updates)`. It raises `RequirementError("plain message")` on
     bad input.
2. Append it to `REQUIREMENTS`.
3. Ask for it at sign-up too, so new accounts aren't prompted a second
   time.
4. Tests: an older account is prompted, a new one isn't, and save, skip
   and failure all behave as described above. See
   `tests/test_account_requirements.py`.

**Changing what a requirement collects:** bump its `version`. That changes
`REQUIREMENTS_VERSION`, so every session re-checks. `is_met` decides who
is asked again, so update it to look for the new data.

## Privacy

- Anything personal goes through `crypto_service` before it is stored,
  as the profile does.
- `collect` returns only what should be written to the account.
- Session updates are for display only, like the first name for the
  greeting. They live in the tenant's own signed cookie.
