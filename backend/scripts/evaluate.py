"""Tiny evaluation harness — gives you real numbers for the results chapter
instead of adjectives like "the answers seemed good".

Reads a JSON file of {question, must_contain[]} and reports:
  * retrieval hit-rate  (did the right chunk make it into top-k context?)
  * answer keyword coverage (did the final answer actually contain it?)
  * mean latency

The retrieval/answer distinction matters: if retrieval hits but the answer
misses, that's a generation problem (prompt/model). If retrieval misses too,
that's a retrieval problem (chunking/threshold/top_k) — see ROADMAP.md Day 3
"If answers are bad, fix in this order".

Run:
    python -m scripts.evaluate --file scripts/eval_set.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from app.services import llm, retriever


async def run(path: Path) -> None:
    cases = json.loads(path.read_text(encoding="utf-8"))
    retrieval_hits = 0
    answer_hits = 0
    latencies: list[float] = []

    for case in cases:
        q = case["question"]
        needles = [n.lower() for n in case.get("must_contain", [])]

        t0 = time.perf_counter()
        hits = await retriever.retrieve(q)
        context = retriever.build_context(hits["text"])
        answer = await llm.generate(q, context)
        latencies.append(time.perf_counter() - t0)

        ctx_l, ans_l = context.lower(), answer.lower()
        if needles and any(n in ctx_l for n in needles):
            retrieval_hits += 1
        if needles and any(n in ans_l for n in needles):
            answer_hits += 1

        mark = "PASS" if any(n in ans_l for n in needles) else "FAIL"
        print(f"[{mark}] {q}\n       -> {answer[:150]}\n")

    n = len(cases) or 1
    print("=" * 60)
    print(f"Retrieval hit-rate : {retrieval_hits}/{n} = {retrieval_hits / n:.1%}")
    print(f"Answer accuracy    : {answer_hits}/{n} = {answer_hits / n:.1%}")
    print(f"Mean latency       : {sum(latencies) / n:.2f}s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="scripts/eval_set.json")
    args = ap.parse_args()
    asyncio.run(run(Path(args.file)))


if __name__ == "__main__":
    main()
