"""Smoke tests without Ollama: a mock server that answers with fixed token probabilities."""
import asyncio
import math

import httpx
import pytest
from hunch_engine_ollama import Ollama

from hunch import engines

NOUL = {"type": "noul", "instructions": "Does `answer` say anything `passages` don't support?"}


def mock(first_token: dict[str, float]) -> httpx.MockTransport:
    def handler(request):
        top = [{"token": t, "logprob": math.log(p)} for t, p in first_token.items()]
        return httpx.Response(200, json={"choices": [{"message": {"content": top[0]["token"]},
                                                      "logprobs": {"content": [{"token": top[0]["token"], "top_logprobs": top}]}}],
                                         "usage": {"prompt_tokens": 12}})
    return httpx.MockTransport(handler)


def run(engine, model, state, questions):
    got = asyncio.run(engine.answer(model, state, questions))
    return engines.check(model, questions, got)  # the shapes hunch stores


def test_generic_prompt_reads_yes_probability():
    a = run(Ollama(mock({"yes": 0.8, "no": 0.2})), "ollama:qwen2.5:0.5b", {"passages": "p", "answer": "a"}, {"q": NOUL})
    assert a["q"]["noul"] == pytest.approx(0.8, abs=1e-3)


def test_template_turns_supported_into_not_unsupported():
    # MiniCheck says Yes (supported) at 0.9, so "does it say anything unsupported?" is yes at 0.1
    a = run(Ollama(mock({"Yes": 0.9, "No": 0.1})), "ollama:bespoke-minicheck#rag-unsupported",
            {"passages": "p", "answer": "a"}, {"q": NOUL})
    assert a["q"]["noul"] == pytest.approx(0.1, abs=1e-3)


def test_template_names_missing_columns():
    with pytest.raises(ValueError, match="passages"):
        run(Ollama(mock({"Yes": 1.0})), "ollama:bespoke-minicheck#rag-unsupported", {"answer": "a"}, {"q": NOUL})


def test_unknown_template_is_named():
    with pytest.raises(ValueError, match="no template 'nope'"):
        run(Ollama(mock({"Yes": 1.0})), "ollama:m#nope", {"passages": "p", "answer": "a"}, {"q": NOUL})
