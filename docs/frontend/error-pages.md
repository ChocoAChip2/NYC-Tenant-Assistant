# Error pages

`templates/error.html` is used for every 4xx/5xx response. It is rendered by
`error_pages.py`, which `create_app()` registers.

## Why it exists

Found by the 2026-09-29 browser sweep. Until then a mistyped URL (404), an
expired form (the CSRF 400), or hitting a rate limit (429) showed Werkzeug's
or Flask-Limiter's bare default page. Those pages had:

- no product name
- no way back into the site
- no legal disclaimer, which `branding-and-disclaimers.md` promises on every
  page

## What it does

- **Copy:** one heading and one sentence per status, in plain words, with a
  next step. Nothing technical and no blame.
- **Links:** the start page and the building lookup, which work whether or
  not the visitor is logged in.
- **Headers:** the exception's own headers are kept, so a 405 still sends
  `Allow` and a 429 sends `Retry-After`.
  `RATELIMIT_HEADERS_ENABLED` turns on the Retry-After and `X-RateLimit-*`
  headers for every response.
- **The chat endpoint:** `/chat/message`, and any JSON request, gets
  `{"error": "..."}` instead of HTML, because the chat page shows the
  `error` field from its fetch.
- **Redirects:** status codes below 400 pass straight through untouched.

## Favicon

`/favicon.ico` serves the ST mark as an SVG (`image/svg+xml`), which Chrome,
Firefox and Safari accept at that path. Without it, every new visit logged a
404 in the console.
