# SideKick Tidbit: handoff (2026-09-28)

For whoever continues this, most likely Claude Code running locally in
`~/Desktop/Github/nyc-tenant-assistant-push-2`. Read this first, then
`docs/legal-sources-catalog.md`.

## 0. Where you're running changes what you can do (read first)

**Cloud containers can't reach the law sources.** Tested 2026-09-28 from
a cloud sandbox: `files.amlegal.com`, `www.nyc.gov`,
`data.cityofnewyork.us` and `*.supabase.co` all blocked. Claude Code on
the web runs in the same kind of container. Plan around it:

| Task | Cloud container (Claude Code on the web) | Owner's Mac (Claude Code CLI in `~/Desktop/Github/nyc-tenant-assistant-push-2`) | GitHub Actions |
|---|---|---|---|
| Write/test the ALP parser | ✅ against `tests/fixtures/alp/` (real excerpts, see its README) | ✅ + full zip | ✅ |
| Parse the **full** Admin Code zip | ❌ can't download it | ✅ (`~/Desktop/Github/sidekick-corpus-work/admin_xml.zip`, or re-download) | ✅ open internet |
| Live NYC Open Data / GeoSearch checks | ❌ | ✅ | ✅ |
| Apply Supabase migrations / load the library | only if a Supabase MCP connection is available | ✅ via Supabase dashboard SQL editor or CLI | ✅ once `CORPUS_INGEST_TOKEN` exists |
| Push / PR | ✅ | ✅ | — |

**Rule of thumb:** build and unit-test anywhere. Anything that needs the
real full file, the live DB or live APIs happens on the Mac or in an
Actions run (`workflow_dispatch` plus a `--dry-run` flag, then read the
logs). **Never fake a live check.** If you can't reach it, say so and
leave it for a machine that can.

Real section counts in the current Admin Code, for sanity minimums: **HMC
211, Rent Stabilization Law 25, Unlawful Eviction 9, Right to Counsel 6,
Human Rights Law 37.** Set each source's minimum to ~90% of these.

## 1. The project

- **SideKick Tidbit**: a free web app for **individual NYC tenants** (decided: tenant-first, not organizers). Flask + Supabase (Auth, Postgres, RLS) + Gemini. Hosted on Render (free tier).
- Repo `ChocoAChip2/NYC-Tenant-Assistant`, branch `main`. Supabase project "Project Paradigm", ref `lwskfyxmcuowexehbqig`.
- **Pushing:** the earlier cloud sandbox could not push (proxy 403), so everything went through `git bundle` → laptop → PR. **Locally you can push directly.** Keep one-branch-per-change and PR → merge. Claude Code on the web can push only a branch, not `main`: open a PR and the owner merges it.
- Commit trailer: `Co-Authored-By: Claude …` plus the session link. PR bodies end with the Claude Code line. `gh secret/auth/api/config` were blocked in that setup, so GitHub secrets must be added by the owner in the web UI.

## 2. State of `main` (as of PR #37, 2026-09-29)

Shipped, in order: SideKick Tidbit rename + ST mark + disclaimers (#25), prominent amber banner + per-message note + referral prompt rule (#26), legal-corpus framework on pgvector (#28), 4-bug hardening sweep (#29), anon-read revoke + keep-alive retarget (#30), **HIBP breached-password check** (#31), **public `/building` lookup** (#32), **real-data fixes to the lookup** (#33), **legal-library parser/CLI/migration** (#34, #35), **refresh hardening + preflight RPC** (#36), **site-sweep fixes: branded error pages, reset-link retry, hand-off arming, contrast, favicon, hermetic tests** (#37).

Test suite: **591 passing** (`python -m unittest discover -s tests`). Tests never touch the network: `tests/__init__.py` turns the HIBP check off (it used to call the real API, which failed 5 tests on a machine with internet).

**The legal library is live (2026-09-29).**
- **Migration:** `20260929_legal_library.sql` was applied after a rolled-back live test of the current version passed. `get_advisors` shows only expected findings: the token-checked anon `corpus_*` functions, RLS with no policies on the two private tables, and public law being visible to GraphQL.
- **Ingest token:** created on the owner's Mac and never printed. Only its sha256 is in `corpus_ingest_tokens`, labelled `owner-mac-and-github-actions-2026-09-29`.
- **Load:** the initial load from the Mac used ALP's zip, `Last-Modified` 29 Sep 2026. It added 308 sections and 521 chunks (18 sections are repealed). A second run changed nothing.
- **Spot checks:** § 27-2029 says sixty-two degrees, with `last_amended` 2017-10-01.
- **Retrieval:** the app's own `RetrievalService`, run against the live data, returns § 27-2029 for "sixty-two degrees" and §§ 27-2031 and 27-2029 for "hot water temperature".
- **Grounding:** still OFF on Render (`LEGAL_CORPUS_ENABLED` is not set).
- **Known gap, fix before relying on grounding:** there are no embeddings yet, and full-text search requires every word to match. So everyday questions find nothing: "no heat at night how cold can it get", "my landlord changed the locks", "mold in my bathroom", "can my landlord evict me without going to court" and "right to counsel eviction lawyer" all return 0 sources. Two fixes:
  - an OR fallback in `search_legal_documents` when the AND query finds nothing, plus tenant-phrase synonyms (landlord→owner, locks→lock)
  - an embeddings backfill: a `corpus_set_embeddings` RPC plus a Gemini batch; `tools/ingest_corpus.py`, the old embedding writer, no longer works against this schema

**Branches:** every branch is merged into `main`. The two superseded AI branches are preserved as `archive/*` tags. A repository rule blocks branch deletion, so old branch refs stay on GitHub as history. Local clones were cleaned on 2026-09-29.

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

**Committed on the branch so far:** `tools/corpus/model.py` (now tested; hash normalizes ASCII whitespace only, to match the DB), `tools/corpus/alp.py` (parser), `tools/corpus/registry.py`, `tools/corpus/refresh.py` (CLI), `supabase/migrations/20260929_legal_library.sql` (**written, NOT applied**), `supabase/checks/20260929_legal_library_checks.sql` (the rolled-back live verification), `tests/fixtures/alp/`, tests for all of it (562 passing), `docs/legal-sources-catalog.md`, this file.

### Decisions already made (with evidence)

1. **Source: American Legal Publishing bulk XML**, the city's official publisher. Admin Code current through Local Law 2026/147. Chapter file IDs, XML structure and what to strip are in the catalog. `§ 27-2029` in the XML matches the nyc.gov PDF (62°F), and its history line `(Am. L.L. 2017/086 … eff. 10/1/2017)` is exactly the amendment the stale readthedocs mirror is missing. **Never ingest mirrors.**
2. **Standard library only** (`xml.etree`, `urllib`, `hashlib`), so it runs on the laptop and in Actions with no installs.
3. **Least-privilege writes, not the service-role key.** Plan: a private `corpus_ingest_tokens` table (RLS on, no policies) storing the **sha256** of a 256-bit token, and `SECURITY DEFINER` RPCs (`search_path` pinned `public, extensions, pg_temp`, using `extensions.digest`) that check the token, callable by anon. A leaked token can touch only the library.
   - `corpus_upsert_sections(p_token, p_run_id, p_sections jsonb)`: **recompute the hash server-side**. New section → insert + log `added`. Changed → replace text/chunks + log `amended`. Unchanged → bump `last_checked_at`. Cap batch size and text length.
   - `corpus_preflight_source(p_token, p_run_id, p_source_key, p_seen_keys text[])`: called **before** a source's upserts. It raises if the parse covers < 80% of the sections active right now, so a bad parse is refused while the library is untouched. Added 2026-09-29.
   - `corpus_finalize_source(p_token, p_run_id, p_source_key, p_seen_keys text[])`: mark unseen active sections `missing_from_source` (**never delete**, since old citations reference them). **Raise if seen < 80% of the sections active before the run.** Rows this run added or restored don't count, so padding with new keys can't get past it. It also raises if the seen list leaves out a section this run wrote. Before the 2026-09-29 fix it counted after the upserts, so 5 real + 20 junk sections passed as 25/30.
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

Done in the cloud session of 2026-09-28 (items 1-4 of the old list, minus everything that needs the internet):

- ✅ `alp.py` meets every item-1 requirement, tested on the fixtures. Unknown markup is kept and reported in `warnings`, never silently dropped. Repeal comes only from ALP's `[Repealed]` heading marker, because "(Repealed and added L.L. …)" is a real form for a section in force.
- ✅ `registry.py`: minimums are 90% of the real counts (HMC 189, RSL 22, UE 8, RTC 5, HRL 33). Rent Control is disabled until its count is measured. NYS sources are listed as data only, with no adapter until the API shape is seen live.
- ✅ `refresh.py`: `--dry-run`, `--source`, `--from-zip`, `--dump DIR`, `--summary PATH`, `--new-token`. Exit codes:
  - 0: ok
  - 1: write failed (the summary says whether anything was written and lists only what was)
  - 2: could not start: usage, credentials, download or state read failed; nothing written
  - 3: gate failed; nothing written

  Timeouts, resets and HTTP 502/503/504 are retried twice. Every failure ends in a summary, never a traceback, and a started run is always closed.
- ✅ Migration + RPCs written. They were smoke-tested on a **local** PostgreSQL 16 + pgvector that mimics Supabase, rolled back; every check passes and six deliberate breakages are each caught. That is **not** the live check.

**Needs a machine with internet (the Mac, or an Actions run), in this order:**

0. ✅ **On GitHub.** Merged to `main` as PR #34.
1. ✅ **Full-zip dry run (2026-09-28, on the Mac).** The first run found three real shapes the fixtures never showed, fixed in `fix/legal-library-full-zip`: `"§ 27- 2017.4."` / `"§ 27- 2017.8"` (space after the hyphen; both pest sections were being skipped), PARA style `EdNote` on § 27-2093.1 (a note about L.L. 2026/138, eff. 4/15/2027, was being kept as law), and `"§ 8-108 Reserved."` / `"§ 8-110 Reserved."` (placeholders, now left out). After the fix, both `--from-zip` and the live download (ALP `Last-Modified: Wed, 23 Sep 2026`) give **exit 0, zero warnings: HMC 211, RSL 25, UE 9, RTC 6, HRL 35, Rent Control 22 = 308**. Rent Control is enabled with `real_count=22`. § 27-2031 has no history line in the full zip either, so the fixture is not truncated there.
2. ✅ **Cross-check against nyc.gov** (DOB PDF dated 2026-04-17). All 211 HMC sections appear in both. §§ 27-2029, 27-2031 and 27-2005 match word for word. The other differences are PDF line-break hyphenation (`high-efficiency` split across lines) and page headers. One real finding: § 27-2115(5) reads "the rules established pursuant to section shall be subject to…" in **both** sources (the section number is missing in the official text itself), and the PDF also drops the rest of that sentence. The ALP text is the more complete of the two.
3. ✅ **Migration, rolled back on the live DB** (2026-09-28, via the Supabase connector). The whole migration plus the checks ran in one transaction and returned "ALL CHECKS PASSED" (10 sections in the transaction). Afterwards `corpus_ingest_tokens` did not exist and `legal_sources` was still empty, so the rollback held. The run used small synthetic payloads with Python-computed hashes instead of the long real ones in the checks file. Real-text hash parity is proven by step 5 anyway: the server recomputes the hash for every one of the 308 sections and rejects any mismatch. **Next:** `apply_migration`, then `get_advisors`.
3b. ✅ **End-to-end load, locally (2026-09-29).**
   - **Setup:** local PostgreSQL 16 with pgvector, pgcrypto and Supabase's roles and default grants, plus a small stand-in for PostgREST that runs every call as `anon` with anon's 3 s statement timeout. The real CLI loaded the real zip through it.
   - **Initial load:** 308 added, in about 1 s. The server recomputed and accepted every section's hash, so real-text parity holds.
   - **Second run:** 0 changes, `changed=false`.
   - **Old heat rule planted:** reported as **amended** (§ 27-2029, chunks replaced).
   - **Extra section planted:** marked **missing**, kept, and no longer searchable.
   - **Wrong token:** exit 1, "nothing was written".
   - **Server down:** exit 2 with a summary.
   - **Anon search for "sixty-two degrees Fahrenheit":** § 27-2029, with the official URL.
   - **Known retrieval gap:** there are no embeddings yet, so search is full-text only, and every word must match. "landlord changed the locks" finds nothing, because § 26-521 says "owner" and "lock".
   - `supabase/checks/…` now covers preflight, padding and seen-list consistency. It was mutation-tested: each of the three rules, when disabled, makes it fail.
4. **Ingest token:** `python -m tools.corpus.refresh --new-token` on the Mac. Run the printed INSERT, which carries only the hash, and paste the token into GitHub secret `CORPUS_INGEST_TOKEN`.
5. **Initial load from the Mac:** set `SUPABASE_URL`, `SUPABASE_KEY` (anon) and `CORPUS_INGEST_TOKEN`, then run `python -m tools.corpus.refresh --from-zip …`. Expect 308 added. Then verify:
   - row counts per `source_key`
   - `nyc-hmc:27-2029` `full_text` says sixty-two and `last_amended = 2017-10-01`
   - `search_legal_documents(NULL, 'sixty-two degrees')` returns § 27-2029
   - `legal_refresh_runs` shows `succeeded`
   - a second identical run reports 0 changes
6. Workflow + `/law` page + chip links + docs + log entry. Ship. **Delete or rewrite `tools/ingest_corpus.py`**: after the migration its `on_conflict="authority,citation"` upsert has no matching constraint and it never sets `section_key`, so it will fail.

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
