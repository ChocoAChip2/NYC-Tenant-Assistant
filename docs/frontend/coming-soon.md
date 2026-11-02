# Coming-soon page and launch list (`coming_soon.py`)

Owner, 2026-11-01: the site is on `sidekicktidbit.com` (DNS on Cloudflare,
pointing at Render) but isn't launched. Only the team should see the real
site; the public sees what it will do and can ask to hear when it opens.

## How it works

- `_coming_soon_gate` is the first `before_request` on `main_bp`. For the
  hosts in `coming_soon.PUBLIC_HOSTS` (`sidekicktidbit.com`,
  `www.sidekicktidbit.com`), `GET /` renders `coming_soon.html`; the
  launch-list POST, Privacy, the language switch and the favicon stay
  open; every other page redirects to `/`. Static files are unaffected.
- The team works on the Render address
  (`tenantrightsandformsassistant.onrender.com`). Every response there
  carries `X-Robots-Tag: noindex, nofollow`. The address is unlisted, not
  locked: anyone who has it can open it. For a real lock, put the domain
  behind Cloudflare Access and stop sharing the Render address (Cloudflare's
  Zero Trust Free plan asks for a payment method even at $0, which the
  owner has to add).
- **Launching:** set `SITE_LAUNCHED=1` in Render. The gate turns off; no
  code change.

## Launch list

- `POST /launch-list`: CSRF, 5 an hour and 20 a day per client, a hidden
  "website" field that only bots fill (answered as if it worked, nothing
  stored).
- The address is lower-cased, checked for shape, encrypted with
  `crypto_service` and sent with an HMAC of it (keyed from the active data
  key) to `join_launch_list()`. Same address twice: one row. With no data
  key configured nothing is stored and the page asks to try again,
  because the page says the address is encrypted.
- Table and function: `supabase/migrations/*_launch_list.sql`. The app's
  public key can only call the function; nobody but the SQL editor can
  read the table. The function refuses anything that isn't an `enc:v`
  envelope and caps the list at 50,000 rows.
- To send the launch email: export from the SQL editor, decrypt with
  `python -m tools.export_launch_list file.csv` (data key set in your
  shell), send, then `delete from public.launch_list;`. The Privacy page
  promises the addresses are deleted after that message.
