"""Measure how often the library finds the right law for a tenant's question.

    python -m tools.corpus.eval_search              # against SUPABASE_URL / SUPABASE_KEY (anon)
    python -m tools.corpus.eval_search --k 6 --min-recall 0.8

Runs every question in tests/fixtures/tenant_questions.json through the
same RPC the app uses (search_legal_documents, text only, no embedding)
and reports recall@k: the share of questions whose top k results include
at least one of the listed sections. Read-only; uses the public anon key.

Exit codes: 0 ok, 1 recall below --min-recall, 2 bad usage or the RPC failed.
Standard library only, like the rest of tools/corpus.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

FIXTURE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                       "tests", "fixtures", "tenant_questions.json")


def search(url: str, key: str, question: str, k: int, opener=urllib.request.urlopen) -> list[str]:
    body = json.dumps({"query_embedding": None, "query_text": question, "match_count": k}).encode("utf-8")
    request = urllib.request.Request(
        url.rstrip("/") + "/rest/v1/rpc/search_legal_documents", data=body, method="POST",
        headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with opener(request, timeout=30) as response:
        rows = json.loads(response.read() or b"[]")
    return [row.get("citation") for row in rows]


def evaluate(cases: list[dict], run) -> tuple[int, list[str]]:
    hits, lines = 0, []
    for case in cases:
        found = run(case["q"])
        hit = bool(set(case["any_of"]) & set(found))
        hits += hit
        lines.append(f"{'HIT ' if hit else 'miss'} {case['q']!r} -> {found}")
    return hits, lines


def main(argv=None, *, env=None, opener=urllib.request.urlopen, out=None) -> int:
    env = os.environ if env is None else env
    out = sys.stdout if out is None else out
    parser = argparse.ArgumentParser(prog="python -m tools.corpus.eval_search")
    parser.add_argument("--k", type=int, default=6, help="results per question (the app uses 6)")
    parser.add_argument("--min-recall", type=float, default=0.0, help="exit 1 below this share, e.g. 0.8")
    args = parser.parse_args(argv)
    url, key = env.get("SUPABASE_URL"), env.get("SUPABASE_KEY")
    if not (url and key):
        out.write("error: needs SUPABASE_URL and SUPABASE_KEY (the public anon key)\n")
        return 2
    with open(FIXTURE, encoding="utf-8") as handle:
        data = json.load(handle)
    run = lambda q: search(url, key, q, args.k, opener)  # noqa: E731
    try:
        results = {name: evaluate(data[name], run) for name in ("tuning", "held_out")}
        noise = [(q, run(q)) for q in data.get("noise", [])]
    except (urllib.error.URLError, OSError, ValueError) as exc:
        out.write(f"error: search failed: {exc}\n")
        return 2
    total = sum(len(data[name]) for name in results)
    found = sum(hits for hits, _ in results.values())
    for name, (hits, lines) in results.items():
        out.write(f"## {name}: {hits}/{len(data[name])}\n" + "\n".join(lines) + "\n\n")
    out.write("## noise (ideally empty)\n" + "\n".join(f"{q!r} -> {r}" for q, r in noise) + "\n\n")
    recall = found / total if total else 0.0
    out.write(f"recall@{args.k}: {found}/{total} = {recall:.0%}\n")
    return 1 if recall < args.min_recall else 0


if __name__ == "__main__":
    sys.exit(main())
