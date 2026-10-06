"""Evaluate retrieval against the labeled error set."""

from __future__ import annotations

import json
from pathlib import Path

from pik_bot.bot import retrieve
from pik_bot.corpus import docs_dir


def eval_path() -> Path:
    override = Path(__file__).resolve().parent / "eval_set.json"
    return override


def evaluate(path: Path | None = None) -> dict:
    cases = json.loads((path or eval_path()).read_text(encoding="utf-8"))
    details = []
    top1 = 0
    top3 = 0
    for case in cases:
        hits = retrieve(case["query"], k=3, docs=docs_dir())
        ids = [hit.id for hit in hits]
        hit1 = bool(ids) and ids[0] == case["expected"]
        hit3 = case["expected"] in ids
        top1 += int(hit1)
        top3 += int(hit3)
        details.append(
            {
                "id": case["id"],
                "expected": case["expected"],
                "actual": ids,
                "top1": hit1,
                "top3": hit3,
            }
        )
    count = len(cases)
    return {
        "cases": count,
        "top1_hits": top1,
        "top3_hits": top3,
        "top1_accuracy": (top1 / count) if count else 0.0,
        "top3_accuracy": (top3 / count) if count else 0.0,
        "details": details,
    }


def main() -> int:
    result = evaluate()
    print(
        json.dumps(
            {key: result[key] for key in ("cases", "top1_hits", "top3_hits", "top1_accuracy", "top3_accuracy")},
            indent=2,
        )
    )
    missed = [item for item in result["details"] if not item["top1"]]
    if missed:
        print("top1 misses:")
        for item in missed:
            print(f"  {item['id']}: expected {item['expected']} actual {item['actual']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
