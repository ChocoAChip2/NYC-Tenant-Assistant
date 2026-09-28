# SideKick Tidbit: handoff (2026-09-28)

For whoever continues this, most likely Claude Code running locally in
`~/Desktop/Github/nyc-tenant-assistant-push-2`. Read this first, then
`docs/legal-sources-catalog.md`.

## 1. The project

- **SideKick Tidbit**: a free web app for **individual NYC tenants** (decided: tenant-first, not organizers). Flask + Supabase (Auth, Postgres, RLS) + Gemini. Hosted on Render (free tier).
- Repo `ChocoAChip2/NYC-Tenant-Assistant`, branch `main`. Supabase project "Project Paradigm", ref `lwskfyxmcuowexehbqig`.
- **Pushing:** the earlier cloud sandbox could not push (proxy 403), so everything went through `git bundle` → laptop → PR. **Locally you can push directly.** Keep one-branch-per-change and PR → merge.
- Commit trailer: `Co-Authored-By: Claude …` plus the session link. PR bodies end with the Claude Code line. `gh secret/auth/api/config` were blocked in that setup, so GitHub secrets must be added by the owner in the web UI.

## 2. State of `main` (as of PR #33)

Shipped, in order: SideKick Tidbit rename + ST mark + disclaimers (#25), prominent amber banner + per-message note + referral prompt rule (#26), legal-corpus framework on pgvector (#28), 4-bug hardening sweep (#29), anon-read revoke + keep-alive retarget (#30), **HIBP breached-password check** (#31), **public `/building` lookup** (#32), **real-data fixes to the lookup** (#33).

Test suite: **465 passing** (`python -m unittest discover -s tests`). Tests never touch the network.

### Things that are true and easy to break

- **Templates carry no HTML/CSS/JS comments** (they're served). Jinja `{# #}` only, each template pointing at `docs/frontend/`. A test enforces it.
- **Legal wording lives in `branding.py`**, never hardcoded in templates (tested).
- **Text on an accent fill uses `var(--on-accent)`, never `#fff`.** White on the dark-mode accent is 2.96:1 and fails WCAG AA (tested).
- **`markdown_service.render_markdown` escapes first.** Its output is injected with `|safe`, so relaxing the order is XSS.
- **The app holds no Supabase service-role key**, by design. The account-deletion purge runs as a `SECURITY DEFINER` pg_cron job instead.
- `crypto_service.encrypt()` treats a value as already-encrypted **only if it decrypts**, not merely if it starts with `enc:`. The prefix check was a real lockout bug (#29).
- Building lookup queries `bbl OR boroid+block+lot`. `bbl`-only showed **0** violations for 4601 Henry Hudson Pkwy; the real number is **138** (#33).
- Violation citation chips are labeled **only when unambiguous** (#33). The old parser put a false "NYC Admin Code" label on 27% of real violations. `tests/fixtures/hpd_violation_descriptions.json` holds 135 real strings across 42 shapes.
- `LEGAL_CORPUS_ENABLED` (grounding on/off) and `LEGAL_GUARD_MODE` (`report` default, `enforce` later) are Render env vars. **Grounding is currently OFF, and the corpus is EMPTY.**

## 3. In progress: the legal library (branch `wip/legal-library`)

**Goal:** load official, verbatim NYC (and later NYS) law into `legal_sources`/`legal_documents`, keep it current automatically (the owner asked for a check "every few months"), and put it to use: `/law/<citation>` pages, violation chips linking to the real text, and chat grounding.

**Committed on the branch so far:** `tools/corpus/__init__.py`, `tools/corpus/model.py` (`Section`, content hash over law text only, whole-paragraph chunk packing, `latest_effective_date` from history lines), `docs/legal-sources-catalog.md`, this file.

### Decisions already made (with evidence)

1. **Source: American Legal Publishing bulk XML**, the city's official publisher. Admin Code current through Local Law 2026/147. Chapter file IDs, XML structure and what to strip are in the catalog. `§ 27-2029` in the XML matches the nyc.gov PDF (62°F), and its history line `(Am. L.L. 2017/086 … eff. 10/1/2017)` is exactly the amendment the stale readthedocs mirror is missing. **Never ingest mirrors.**
2. **Standard library only** (`xml.etree`, `urllib`, `hashlib`), so it runs on the laptop and in Actions with no installs.
3. **Least-privilege writes, not the service-role key.** Plan: a private `corpus_ingest_tokens` table (RLS on, no policies) storing the **sha256** of a 256-bit token, and `SECURITY DEFINER` RPCs (`search_path` pinned `public, extensions, pg_temp`, using `extensions.digest`) that check the token, callable by anon. A leaked token can touch only the library.
   - `corpus_upsert_sections(p_token, p_run_id, p_sections jsonb)`: **recompute the hash server-side**. New section → insert + log `added`. Changed → replace text/chunks + log `amended`. Unchanged → bump `last_checked_at`. Cap batch size and text length.
   - `corpus_finalize_source(p_token, p_run_id, p_source_key, p_seen_keys text[])`: mark unseen active sections `missing_from_source` (**never delete**, since old citations reference them). **Raise if seen < 80% of the active count** (a broken parse must not wipe the library).
   - Optional `corpus_set_embeddings(p_token, items)` to backfill vectors later.
   - Generate the token **on the owner's machine** and store only its hash via SQL, so the token never appears in a chat transcript. The owner pastes it into GitHub secret `CORPUS_INGEST_TOKEN`.
4. **Schema changes needed** (new migration): on `legal_sources` add `section_key text unique` (`<source_key>:<citation>`, needed because §26-1301 is duplicated), `source_key`, `heading_path`, `full_text`, `history jsonb`, `notes jsonb`, `repealed bool`, `last_amended date`, `last_checked_at timestamptz`, `status text default 'active'`. **Drop the old `UNIQUE(authority, citation)`** (table is empty). Add a `legal_source_changes` table (section_key, change_type, old_hash, new_hash, detected_at, run_id; public read) and a private `legal_refresh_runs`. Make `search_legal_documents` **exclude non-active sources**. The table currently has 0 rows, so this is safe.
5. **Sanity gates in the client before any write:** a minimum section count per source (HMC ≥ 150), required anchors present (e.g. HMC `27-2001, 27-2029, 27-2031`; RSL `26-501, 26-511`; UE `26-521`), no empty non-repealed sections, and ≥ 80% of the current active count. Fail loudly and write nothing.
6. **Quarterly refresh**: `.github/workflows/refresh-legal-library.yml`, cron `0 9 1 1,4,7,10 *` plus `workflow_dispatch`. Downloads the ALP zip, parses, gates, upserts, finalizes, writes a Markdown change summary to `$GITHUB_STEP_SUMMARY`, and **opens a GitHub issue when anything changed** (`permissions: issues: write`). A failed run emails the owner by default. Note that GitHub disables scheduled workflows after 60 days with no repo activity (same caveat as the keep-alive).
7. **Use it:**
   - public `/law/<citation>` page: verbatim text, heading path, amendment history, "verified current as of <last_checked_at>", link to ALP
   - building-page chips link there when the section exists (one batched query per page)
   - once loaded, set `LEGAL_CORPUS_ENABLED=1` on Render (full-text search works without embeddings), keep guard in `report`, watch the logs, then `enforce`

### Next steps, in order

1. `tools/corpus/alp.py`: parse the ALP XML per the catalog. **Test against the real file** (download it yourself; it's also at `~/Desktop/Github/sidekick-corpus-work/admin_xml.zip`). Cross-check HMC sections against the nyc.gov PDF text (`hmc.pdf` in the same folder); agreement on unchanged sections validates the extraction.
2. `tools/corpus/registry.py`: sources (HMC, RSL, UE, RTC, HRL Title 8 Ch 1, Rent Control), with anchors, minimums and file IDs. Add NYS sources behind `NYSENATE_API_KEY`, and **verify the Open Legislation response shape live before trusting the adapter**.
3. Migration + RPCs. Test each in a rolled-back `BEGIN … ROLLBACK` against the live DB before `apply_migration`, then check `get_advisors`.
4. `tools/corpus/refresh.py` CLI (`--dry-run`, `--source`, `--from-zip PATH`), plus tests with faked HTTP.
5. Initial load from the laptop, then verify row counts, spot-check § 27-2029 in the DB, and run a full-text query through `search_legal_documents`.
6. Workflow + `/law` page + chip links + docs + log entry. Ship.

## 4. What the owner needs to do

- [ ] Register a free **NY Senate Open Legislation API key** (legislation.nysenate.gov), needed for RPL/RPAPL/GOL/MDL/Good Cause.
- [ ] Add GitHub secrets: `CORPUS_INGEST_TOKEN` (generated in step 3 above), optionally `GEMINI_API_KEY` (embeddings) and `NYSENATE_API_KEY`. `SUPABASE_URL` and `SUPABASE_KEY` already exist for the keep-alive.
- [ ] After the library loads: set `LEGAL_CORPUS_ENABLED=1` on Render.
- [ ] Post-deploy checks on Render: sign up with password `password` (should be refused by the HIBP check), and look up one real building on `/building`.
- [ ] Decide whether `/building` becomes the site root. Check Supabase's email-confirmation redirect first; it may point at `/`.

## 5. Roadmap after the library

- **Files panel + one-button case packet** (the owner asked for this early): Supabase Storage, app-side encryption, a `user_files` table with cascade + purge (Storage objects have no FK, so the account purge would miss them otherwise).
- **"Who is my landlord"** from registration contacts (`feu5-w2e2`), plus litigation/harassment findings (`59kj-x8nc`) and AEP status on `/building`.
- **"Is my building rent-stabilized?"** from the RGB/HCR building lists. Surface **RGB Order #58: 0% / 0%** for renewals starting 2026-10-01.
- Ground the chat on the live building record server-side, not just the tenant's first message.
- Timeline + follow-up email; Spanish first; RCNY Title 28 from the Rules zip.
- Known residual gap: condo units HPD recorded under a unit lot with no `bbl`.

## 6. Working habits that paid off

- **Verify against real data, not idealized fixtures.** Fixtures modeled on 2014 records hid two production bugs that 5,000 real rows exposed in minutes.
- **Look at rendered screenshots.** "Nov Sent Out" (reads as November) and the dark-mode contrast failure were both found by eye.
- **Cross-check claims, including your own and a research agent's.** The "no bbl column" claim was wrong, but checking it found the real bug.
- Test SQL in rolled-back transactions against the live DB before applying. `CREATE OR REPLACE` can't change a function's return type; only the live apply revealed that.
- Scratch/probe scripts from this session are in `~/Desktop/Github/sidekick-corpus-work/` (ALP zip, HMC PDF, 5,000-row description sample, probes).
