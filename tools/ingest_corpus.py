"""Loads sections of NYC/NY housing law into Supabase for retrieval.

RUN THIS OFFLINE, NOT FROM THE WEB APP. It needs the Supabase service-role
key, which the app deliberately does not hold (see docs/data-encryption.md
and the account-deletion migration, both of which depend on the app not
having it). Export it for this script only:

    export SUPABASE_URL=...
    export SUPABASE_SERVICE_ROLE_KEY=...
    export GEMINI_API_KEY=...
    python -m tools.ingest_corpus --file corpus/hmc-title27-ch2.json

WHICH LAW, AND WHY NOT THE PENAL CODE
Tenant questions are answered by the Housing Maintenance Code (NYC Admin
Code Title 27, Ch. 2), Real Property Law 235-b, RPAPL Article 7 and NYC
Admin Code Title 26 Ch. 5 for illegal lockouts. Criminal law touches
tenancy at essentially one point, and that hook is in the Admin Code.
Ingesting the penal code would cost real effort and answer almost nothing.

CHUNKING: THE SECTION IS THE CITABLE UNIT
gemini-embedding-001 caps input at 2048 tokens and real sections run from
one sentence to several pages, so long sections are split. Every chunk
keeps its parent section as its citation, so a quote from subsection (c)
still cites "27-2029" rather than "27-2029 chunk 3" -- which is not a
thing a tenant can look up.

INPUT FORMAT
A JSON array of sections, each:

    {
      "authority":    "NYC Administrative Code",
      "citation":     "27-2029",
      "title":        "Minimum temperature to be maintained",
      "jurisdiction": "NYC",
      "official_url": "https://codelibrary.amlegal.com/...",
      "text":         "..."
    }

Scraping is left out of this script on purpose: publishers change their
markup, and a fetch loop that breaks silently would quietly poison the
corpus. Fetch by hand or with a separate throwaway script, eyeball the
result, then load it here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

MAX_CHARS_PER_CHUNK = 4000
CHUNK_OVERLAP_CHARS = 200
EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMENSIONS = 768

REQUIRED_FIELDS = ("authority", "citation", "title", "official_url", "text")


def chunk_section(text: str) -> list[str]:
    """Split a long section, keeping sentences whole where possible."""
    text = text.strip()
    if len(text) <= MAX_CHARS_PER_CHUNK:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + MAX_CHARS_PER_CHUNK, len(text))
        if end < len(text):
            window = text.rfind(". ", start + MAX_CHARS_PER_CHUNK // 2, end)
            if window != -1:
                end = window + 1
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - CHUNK_OVERLAP_CHARS, start + 1)
    return [chunk for chunk in chunks if chunk]


def embed(client, texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for index, text in enumerate(texts):
        response = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=text,
            config={"output_dimensionality": EMBEDDING_DIMENSIONS},
        )
        vectors.append(list(response.embeddings[0].values))
        # The free tier rate-limits embeddings the same way it does
        # generation. Ingest is a one-off; being slow is free.
        if index % 10 == 9:
            time.sleep(1.0)
    return vectors


def validate(sections: list[dict]) -> list[str]:
    problems = []
    seen = set()
    for index, section in enumerate(sections):
        if not isinstance(section, dict):
            problems.append(f"section {index}: not an object")
            continue
        for field in REQUIRED_FIELDS:
            value = section.get(field)
            # Type-checked, not just truthiness: a citation that arrived as
            # the number 27 instead of the string "27-2029" would pass a
            # bare truthiness check and then be stored as a citation nobody
            # can look up.
            if not isinstance(value, str) or not value.strip():
                problems.append(f"section {index}: missing or non-text {field}")
        key = (section.get("authority"), section.get("citation"))
        if key in seen:
            problems.append(f"section {index}: duplicate citation {key[1]}")
        seen.add(key)
        url = str(section.get("official_url", ""))
        if url and not url.startswith("https://"):
            problems.append(f"section {index}: official_url is not https")
    return problems


def ingest(sections: list[dict], supabase, gemini, dry_run: bool = False) -> dict:
    stats = {"sections": 0, "chunks": 0}

    for section in sections:
        body = section["text"].strip()
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()

        source_row = {
            "authority": section["authority"],
            "citation": section["citation"],
            "title": section["title"],
            "jurisdiction": section.get("jurisdiction", "NYC"),
            "official_url": section["official_url"],
            "effective_date": section.get("effective_date"),
            "content_hash": digest,
        }

        chunks = chunk_section(body)
        stats["sections"] += 1
        stats["chunks"] += len(chunks)

        if dry_run:
            continue

        stored = (
            supabase.table("legal_sources")
            .upsert(source_row, on_conflict="authority,citation")
            .execute()
        )
        source_id = stored.data[0]["id"]

        # Replace this section's chunks wholesale. Re-running the ingest
        # after the law changes must not leave the old text retrievable
        # beside the new: two contradictory passages, both cited, is worse
        # than either alone.
        supabase.table("legal_documents").delete().eq("source_id", source_id).execute()

        heading = f"{section['authority']} {section['citation']} -- {section['title']}"
        vectors = embed(gemini, [f"{heading}\n\n{chunk}" for chunk in chunks])

        supabase.table("legal_documents").insert(
            [
                {
                    "source_id": source_id,
                    "source_name": heading,
                    "heading_path": heading,
                    "ordinal": ordinal,
                    "text_content": chunk,
                    "content_hash": hashlib.sha256(chunk.encode("utf-8")).hexdigest(),
                    "embedding": vector,
                }
                for ordinal, (chunk, vector) in enumerate(zip(chunks, vectors))
            ]
        ).execute()

    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="JSON array of sections")
    parser.add_argument("--dry-run", action="store_true", help="validate and chunk, write nothing")
    args = parser.parse_args(argv)

    with open(args.file, encoding="utf-8") as handle:
        sections = json.load(handle)

    problems = validate(sections)
    if problems:
        for problem in problems:
            print(f"ERROR {problem}", file=sys.stderr)
        return 1

    if args.dry_run:
        stats = ingest(sections, None, None, dry_run=True)
        print(f"OK {stats['sections']} sections -> {stats['chunks']} chunks (nothing written)")
        return 0

    url = os.environ.get("SUPABASE_URL")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not (url and service_key and gemini_key):
        print(
            "SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and GEMINI_API_KEY must all be set.",
            file=sys.stderr,
        )
        return 2

    from google import genai
    from supabase import create_client

    stats = ingest(sections, create_client(url, service_key), genai.Client(api_key=gemini_key))
    print(f"OK {stats['sections']} sections -> {stats['chunks']} chunks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
