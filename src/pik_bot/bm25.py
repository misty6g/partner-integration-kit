"""Small, deterministic BM25 ranker. No network and no model files."""

from __future__ import annotations

import math
import re
from collections import Counter

from pik_bot.corpus import Section

_TOKEN = re.compile(r"[a-z0-9_]+")
_STOP = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "our",
        "that",
        "the",
        "this",
        "to",
        "was",
        "we",
        "with",
        "you",
        "your",
    }
)


def tokenize(text: str) -> list[str]:
    return [token for token in _TOKEN.findall(text.lower()) if token not in _STOP and len(token) > 1]


class BM25:
    def __init__(self, sections: list[Section], *, k1: float = 1.5, b: float = 0.75) -> None:
        self.sections = sections
        self.k1 = k1
        self.b = b
        self.docs = [tokenize(section.text) for section in sections]
        self.df: Counter[str] = Counter()
        for tokens in self.docs:
            self.df.update(set(tokens))
        self.avgdl = (sum(len(tokens) for tokens in self.docs) / len(self.docs)) if self.docs else 0.0
        self.n = len(self.docs)

    def score(self, query: str) -> list[tuple[Section, float]]:
        tokens = tokenize(query)
        scored: list[tuple[Section, float]] = []
        for section, doc in zip(self.sections, self.docs, strict=True):
            if not doc or self.avgdl == 0:
                scored.append((section, 0.0))
                continue
            frequencies = Counter(doc)
            total = 0.0
            dl = len(doc)
            for token in tokens:
                tf = frequencies.get(token, 0)
                if tf == 0:
                    continue
                df = self.df.get(token, 0)
                idf = math.log(1 + (self.n - df + 0.5) / (df + 0.5))
                denom = tf + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                total += idf * (tf * (self.k1 + 1) / denom)
            scored.append((section, total))
        scored.sort(key=lambda item: (-item[1], item[0].id))
        return scored

    def top(self, query: str, k: int = 3) -> list[tuple[Section, float]]:
        return self.score(query)[:k]
