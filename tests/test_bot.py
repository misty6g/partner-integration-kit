"""Retrieval-only help bot and the optional LLM path."""

from __future__ import annotations

import json

import httpx
import pytest

from pik_bot.bot import answer, complete_with_llm, retrieve
from pik_bot.corpus import load_sections, slug
from pik_bot.__main__ import evaluate


def test_section_slugs_match_the_error_doc_urls():
    ids = {section.id for section in load_sections()}
    assert "authentication.md#invalid-api-key" in ids
    assert "webhooks.md#timestamp-outside-tolerance" in ids
    assert slug("Invalid order transition") == "invalid-order-transition"


def test_labeled_retrieval_accuracy():
    result = evaluate()
    assert result["cases"] == 16
    assert result["top1_accuracy"] >= 0.8
    assert result["top3_accuracy"] >= 0.9
    assert result["top1_hits"] == result["cases"]


def test_answer_is_deterministic_without_an_llm_key(monkeypatch):
    monkeypatch.delenv("PIK_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    first = answer("409 duplicate_external_id An order with this external_id already exists for the partner.")
    second = answer("409 duplicate_external_id An order with this external_id already exists for the partner.")
    assert first.mode == "retrieval"
    assert first.suggestion == second.suggestion
    assert first.sections[0].id == "orders.md#duplicate-external-id"
    assert "external_id" in first.suggestion
    with pytest.raises(ValueError):
        answer("   ")


def test_llm_failure_falls_back_to_retrieval(monkeypatch):
    monkeypatch.setenv("PIK_LLM_API_KEY", "test-key")

    def explode(_query, _hits):
        raise RuntimeError("upstream down")

    monkeypatch.setattr("pik_bot.bot.complete_with_llm", explode)
    result = answer("400 invalid_cursor The starting_after cursor does not match a resource for this partner.")
    assert result.mode == "retrieval"
    assert result.llm_error.startswith("RuntimeError")
    assert result.sections[0].id == "pagination.md#invalid-pagination-cursor"


def test_llm_success_uses_the_model_text(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("PIK_LLM_API_KEY", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body["temperature"] == 0
        assert "invalid_api_key" in body["messages"][1]["content"]
        return httpx.Response(200, json={"choices": [{"message": {"content": "Send the full secret."}}]})

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr("pik_bot.bot.httpx.Client", factory)
    hits = retrieve("401 invalid_api_key The API key was not recognized.")
    text = complete_with_llm("401 invalid_api_key The API key was not recognized.", hits)
    assert text == "Send the full secret."
    result = answer("401 invalid_api_key The API key was not recognized.")
    assert result.mode == "llm"
    assert result.suggestion == "Send the full secret."
