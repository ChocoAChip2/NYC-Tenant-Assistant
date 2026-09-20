# Branding and disclaimers

## The name and the mark live in `branding.py`

`product_name`, `logo_monogram`, `short_disclaimer`, `top_disclaimer`,
`per_message_disclaimer` and `learn_more_label` are **not** written into any
template. They come from `branding.py` through
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

## Three layers, three jobs

There are now three disclaimers, and they are not redundant — each one
covers a gap the others leave open.

| Layer | Constant | Where | Job |
| --- | --- | --- | --- |
| Banner | `TOP_DISCLAIMER` | Amber bar under the header, chat + settings | Catch someone who is actively looking for a lawyer, **before** they start typing, and tell them what to do about it |
| Per-message | `PER_MESSAGE_DISCLAIMER` | Under every assistant reply | Make sure no single reply can be screenshotted, pasted or quoted without the qualifier attached to it |
| Footer | `SHORT_DISCLAIMER` | Bottom of every page | The standing notice, unobtrusive, always true |

The visual weight is deliberately inverted against the repetition: the
banner appears once and is loud (`--warning` on `--warning-tint`, a 4px
left rule, a round `!` glyph); the per-message note repeats on every turn
and is deliberately quiet (11px, `--muted`, italic, a small amber dot).
A paragraph repeated twenty times is a paragraph nobody reads.

Measured contrast for the banner is 5.04:1 in light mode and 7.77:1 in
dark — both above WCAG AA for body text. It is **not** dismissible, and a
test asserts there is no close button in it: a legal notice with an X is a
legal notice nobody sees.

### The banner makes a promise

The banner tells tenants that if they say they are seeking legal
assistance, the chatbot will give them a list of places to contact. That
promise is kept in `ai_service.py`, rule 7 of the system prompt, which
interpolates `branding.HELP_RESOURCES` in full with phone numbers and
forbids the assistant from inventing any other organisation or substituting
a vague "consult an attorney".

Three things have to stay in sync: the banner text, the prompt rule, and
the resource list. `tests/test_prominent_disclaimers.py::ReferralPromiseTests`
fails if the prompt loses the rule or a resource drops out of it — because
a banner promising something the product does not do is worse than no
banner.

## Where the short disclaimer appears

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
