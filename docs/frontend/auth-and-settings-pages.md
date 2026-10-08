# Auth pages and Settings

Covers `login.html`, `signup.html`, `forgot_password.html`,
`reset_password.html` and `settings.html`. Shared conventions (tokens,
theming, CSRF, submit-disabling) are in [README.md](README.md); rules that
look removable and aren't are in [css-gotchas.md](css-gotchas.md).

The four auth pages share a card layout and are **light-only** — dark mode
is a `chat.html` / `settings.html` feature. They each repeat the same
`:root` token block, so a palette change has to be applied to all of them by
hand.

---

## Password show/hide toggle

Present on `login`, `signup`, `settings` (two fields) and `reset_password`
(two fields), duplicated identically in each — there is no shared template.

The button sits *inside* the input's box (`.password-field { position:
relative }` + `padding-right` on the input) rather than beside it. It swaps
between two inline SVGs via `innerHTML`: an open eye when the password is
hidden, a crossed-out eye when it's visible, with `aria-label` and `title`
updated to match.

**Why SVG and not emoji.** These were 👁️ / 🙈. An emoji renders as a
fixed-color picture that ignores CSS entirely, so it looked identical — and
about equally illegible — against both a light and a dark background. The
SVGs use `stroke="currentColor"`, so the icon follows
`.password-toggle`'s own `color: var(--muted)` (and `var(--text)` on hover),
exactly like every other icon-ish thing on the page. On `settings.html`,
which has dark mode, that means the icon actually tracks the theme.

---

## `reset_password.html` — recovery tokens live in the URL fragment

Supabase appends the recovery tokens to the URL **fragment**:

```
/reset-password#access_token=…&refresh_token=…&type=recovery
```

Browsers never send a fragment to the server, so Flask cannot see it. The
page's script reads the fragment, copies the tokens into hidden form fields,
and only then reveals the form. There is no server-side session at this
point — the visitor followed an emailed link, not a login — so **those
tokens are the only proof of identity `/reset-password` has**.

If the tokens are absent (link already used, expired, or someone navigated
here directly), the script shows an "invalid or expired" notice instead of a
form that cannot work.

Once captured, the tokens are cleared from the visible URL with
`history.replaceState` — they're sensitive and would otherwise sit in
browser history.

This page is also why `[hidden] { display: none !important; }` exists; see
[css-gotchas.md](css-gotchas.md#hidden--display-none-important).

---

## `settings.html`

### Theme radios

Write to the same `localStorage.theme` key that the pre-paint scripts on
this page and `chat.html` read. "Match system" *removes* the key and the
`data-theme` attribute rather than storing a `"system"` value, so the
`prefers-color-scheme` media query takes over.

### Your name

The same three fields as sign-up (first name, last name, date of birth),
with the same confidentiality note, so accounts made before sign-up asked
for a name can add one. It is pre-filled from the account's encrypted
profile, read fresh from Supabase auth on each visit. If that read fails
(an expired token, auth down), the form shows empty but the hint does not
claim there is no name on file. Saving goes to `POST /settings/profile`,
which validates with `profile_service`, writes the envelope through the
tenant's own session (`supabase_service.update_profile`), keeps any
rotated tokens, and updates the chat greeting straight away.

### The account form is selected by id

The "Saving…" submit handler targets `#account-form` and `#profile-form` by id, **not**
`form.stack` — the delete-account confirmation below is also a `.stack`
form, and a `querySelector("form.stack")` would eventually claim the wrong
one.

### Danger Zone — account deletion

Sits alone at the bottom of the page, bordered and titled in `--error`, so
it doesn't read like "Save account changes" one card up.

The flow is two deliberate confirmations:

1. **"Delete my account"** reveals the confirmation form. It deletes
   nothing.
2. The form's submit button stays **disabled** until the typed email matches
   the signed-in address (case- and whitespace-insensitive) **and** the
   acknowledgement checkbox is ticked.

Both conditions are re-checked in `request_account_deletion()`. The
client-side gating is about making the second confirmation deliberate — a
crafted POST skips it entirely, and "your account and every conversation in
it" is not something to act on from one unverified request.

Submitting schedules deletion 30 days out; nothing is deleted then either.
While a request is pending, the delete control is replaced by a banner with
the exact date and a one-click **Cancel deletion**. Cancelling has no
confirmation gate on purpose — the risky direction is scheduling a deletion,
not stopping one.

The banner's date is rendered server-side as plain UTC (so it's never blank
or wrong without JS) and then rewritten client-side into the visitor's own
locale and timezone.

The actual deletion runs as a `pg_cron` job inside Postgres, not in this
app — see `supabase/migrations/20260907033439_account_deletion_requests.sql` and
`log/2026-09-07-account-deletion-and-message-cap.txt`.

---

## `signup.html`

Renders an "an account already exists for this email" notice when
`routes.py` sets `existing_account_email`. Supabase does not always report a
duplicate signup directly, so the route detects it and passes it through —
otherwise someone gets told to "check your email" for a confirmation that
was never sent.

### Name and date of birth

The form asks for first name, last name and date of birth above the email
field, with a lock-icon note directly under them saying the details are
kept confidential. The note's promises are what the code does, so keep
them in step with `profile_service.py`:

- **"encrypted before they're stored"** — the three fields go into the new
  account's Supabase `user_metadata` as one AES-GCM envelope
  (`crypto_service`). If encryption isn't configured, nothing is stored.
- **"used only to greet you and confirm your age"** — the first name is
  decrypted at login into the session and shown as "Good morning, Ana" in
  chat (both the Jinja greeting and the time-of-day script prefer it over
  the email handle). The date of birth is only checked at sign-up.
- **"never sent to the AI"** — neither field is read by the chat route.
- **"deleted with your account"** — it lives on the `auth.users` row the
  account-deletion purge removes.

The date picker's `max` is the latest birthday that is 13 today
(`profile_service.latest_allowed_birthday`, which handles Feb 29), and
`min` is 120 years back. Both are re-checked server-side before the
breach check and before Supabase is called, so an under-13 sign-up never
creates an account. On any error the form keeps what was typed (email,
names, date) — never the password.
