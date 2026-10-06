"""Integration-help bot.

With no LLM key this returns the best runbook fix (retrieval mode). When
``PIK_LLM_API_KEY`` or ``OPENAI_API_KEY`` is set, the same sections are sent
to an OpenAI-compatible chat endpoint. Any LLM failure falls back to retrieval.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import httpx

from pik_bot.bm25 import BM25
from pik_bot.corpus import Section, load_sections


@dataclass
class Hit:
    id: str
    title: str
    source: str
    score: float
    fix: str

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "source": self.source,
            "score": round(self.score, 4),
            "fix": self.fix,
        }


@dataclass
class Answer:
    mode: str
    query: str
    suggestion: str
    sections: list[Hit]
    llm_error: str | None = None

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "query": self.query,
            "suggestion": self.suggestion,
            "llm_error": self.llm_error,
            "sections": [section.as_dict() for section in self.sections],
        }


def llm_api_key() -> str | None:
    return os.environ.get("PIK_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or None


def retrieve(query: str, *, k: int = 3, docs: Path | None = None) -> list[Hit]:
    index = BM25(load_sections(docs))
    hits: list[Hit] = []
    for section, score in index.top(query, k=k):
        if score <= 0:
            continue
        hits.append(Hit(id=section.id, title=section.title, source=section.source, score=score, fix=section.fix))
    return hits


def answer(query: str, *, k: int = 3, docs: Path | None = None) -> Answer:
    question = query.strip()
    if not question:
        raise ValueError("query must not be empty")
    hits = retrieve(question, k=k, docs=docs)
    suggestion = hits[0].fix if hits else "No matching runbook section. Start with docs/errors.md and include the error code."
    mode = "retrieval"
    llm_error = None
    if llm_api_key():
        try:
            suggestion = complete_with_llm(question, hits)
            mode = "llm"
        except Exception as exc:  # network and API failures stay usable offline
            llm_error = f"{exc.__class__.__name__}: {exc}"
    return Answer(mode=mode, query=question, suggestion=suggestion, sections=hits, llm_error=llm_error)


def complete_with_llm(query: str, hits: list[Hit]) -> str:
    key = llm_api_key()
    if not key:
        raise RuntimeError("No LLM API key is configured.")
    base = os.environ.get("PIK_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("PIK_LLM_MODEL", "gpt-4o-mini")
    excerpts = "\n\n".join(f"[{hit.id}] {hit.title}\nFix: {hit.fix}" for hit in hits) or "(no runbook sections)"
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are the Partner Integration Kit support assistant. "
                    "Use only the runbook excerpts. Reply with a short fix in plain text. "
                    "If the excerpts do not apply, say what information is missing."
                ),
            },
            {"role": "user", "content": f"Error:\n{query}\n\nRunbook excerpts:\n{excerpts}"},
        ],
    }
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json=payload,
        )
        response.raise_for_status()
        body = response.json()
    content = body["choices"][0]["message"]["content"]
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("LLM response did not include text.")
    return content.strip()


def section_ids(sections: list[Section] | None = None) -> list[str]:
    return [section.id for section in (sections or load_sections())]
