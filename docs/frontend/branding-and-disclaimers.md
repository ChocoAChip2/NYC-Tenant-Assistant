# Branding and disclaimers

## The name and the mark live in `branding.py`

`product_name`, `logo_monogram`, `short_disclaimer` and `learn_more_label`
are **not** written into any template. They come from `branding.py` through
a context processor registered by `branding.register(app)`, which both
`create_app()` and the test helper call.

That indirection is deliberate and there are tests enforcing it
(`tests/test_branding_and_disclaimers.py`):

- **The name**, because this app has already been renamed once. It was
  hardcoded in six files and the rename touched thirteen strings. Now it is
  one edit.
- **The disclaimer**, because it is the only text in the app with legal
  weight. Six hand-copied copies drift, and the copy that drifts is the one
  that gets quoted back at you.

`ST` is an interim monogram, not a final logo. It renders as white text in
the accent-colored `.brand-mark` square that the auth pages already had, so
replacing it with a real mark means changing one element, not a layout.

## Where the disclaimer appears

Every page carries the short version. Placement differs because the pages
do:

| Page | Placement |
| --- | --- |
| login, signup, forgot, reset | Under the card. The card is wrapped in `.page` so the note sits beneath it rather than inside the white box. |
| settings | Last element in `<main>`. |
| chat | Under the composer, below the character counter — the placement ChatGPT and Claude both use, and the one the request was modelled on. It sits outside the `{% if active_conversation_id %}` branch so the greeting state shows it too. |
| learn-more | In a real `<footer>`, since that page is about the disclaimer. |

## When the assistant says it out loud

The footer is the standing notice. The assistant states it **in its own
reply** only when the stakes are real — an active or threatened court case,
a deadline, a signed document, money at risk, or a decision that is hard to
reverse. Those triggers are listed in `branding.ESCALATION_TRIGGERS` and
interpolated into the system prompt, so the product decision about *when*
lives next to the words themselves.

The prompt also explicitly forbids attaching a disclaimer to routine
factual questions. Both halves matter: stapling the same sentence onto
"what temperature must my landlord keep the heat at" is what trains people
to skip past it on the message where it counts.

## The Learn More page

`/learn-more` is **public on purpose**. Someone deciding whether to trust
this with their housing situation should be able to read what it claims to
be before handing over an email address, and someone who has just been told
something alarming should be able to find a real lawyer without logging in.

It carries the full disclaimer, an explicit can/cannot list, and free or
low-cost help resources — all from `branding.py`. Outbound resource links
use `target="_blank"` with `rel="noopener noreferrer"` so a tenant
mid-conversation does not lose it by tapping a helpline.
