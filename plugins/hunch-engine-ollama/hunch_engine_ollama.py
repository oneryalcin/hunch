"""A hunch engine for local models served by Ollama (https://ollama.com), as a plugin.

`model: ollama:<name>` (e.g. `ollama:qwen2.5:0.5b`) asks the model through Ollama's OpenAI-compatible API and reads
the answer from the first answer token's probabilities: the same prompt and reading as hunch's built-in LLM engine
(`core.llm_prompt`, `core.llm_answer`), so a local model and a hosted one differ only in the model.

`model: ollama:<name>#<template>` asks in a model's own format instead, from TEMPLATES below. A specialised model
answers one question its own way: Bespoke-MiniCheck says whether a document supports a claim, and asked hunch's
generic question it answered its own (AUROC 0.265 on the rag-answers battery, reliably backwards). The template is
part of the model string, so its answers have their own cache keys and their own row in the benchmark.

One request per question; local, so it costs nothing and runs one row at a time. OLLAMA_HOST moves the server.
"""
import asyncio
import hashlib
import json
import os

import httpx

from hunch import core

# name → how to ask. `prompt` is filled from the row's state columns; the model answers Yes or No. `yes_is` is the
# hunch answer a Yes means, for a spec whose question is the model's turned around.
TEMPLATES = {
    # Bespoke-MiniCheck: "Document: …\nClaim: …" → Yes if the document supports the claim. The rag-answers battery
    # asks the opposite ("does `answer` say anything `passages` don't support?"), so its Yes is our no.
    "rag-unsupported": {"prompt": "Document: {passages}\nClaim: {answer}", "yes_is": "no"},
}


class Ollama:
    # in every answer's cache key: changes with any template, and by hand when the generic prompt or reading changes
    adapter = "ollama-logprobs-v1+" + hashlib.sha256(json.dumps(TEMPLATES, sort_keys=True).encode()).hexdigest()[:8]
    concurrency = 1  # one model on one machine: requests in parallel only queue inside Ollama

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self.base = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        self.transport = transport  # tests pass a mock

    async def answer(self, model: str, state, questions: dict) -> dict:
        name, _, template = model.split(":", 1)[1].partition("#")
        if template and template not in TEMPLATES:
            raise ValueError(f"{model}: no template {template!r} (have {', '.join(TEMPLATES)})")
        async with httpx.AsyncClient(timeout=300, transport=self.transport) as client:
            got = await asyncio.gather(*[self._one(client, name, TEMPLATES.get(template), q, state)
                                         for q in questions.values()])
        return {"answers": dict(zip(questions, (a for a, _ in got))), "tokens": sum(t for _, t in got), "cost": 0.0}

    async def _one(self, client, name: str, template: dict | None, q: dict, state) -> tuple[dict, int]:
        if template:
            if q["type"] != "noul":
                raise ValueError(f"template answers yes/no questions; this one is a {q['type']}")
            missing = [c for c in _fields(template["prompt"]) if not isinstance(state, dict) or c not in state]
            if missing:
                raise ValueError(f"template needs the state columns {missing}")
            prompt, codes, labels = template["prompt"].format(**state), ["yes", "no"], ["yes", "no"]
        else:
            prompt, codes, labels = core.llm_prompt(q, state)
        r = await client.post(f"{self.base}/v1/chat/completions", json={
            "model": name, "messages": [{"role": "user", "content": prompt}], "max_tokens": 8,
            "temperature": 1, "logprobs": True, "top_logprobs": 20})
        r.raise_for_status()
        j = r.json()
        lp = (j["choices"][0].get("logprobs") or {}).get("content") or []
        a = core.llm_answer({"type": "noul"} if template else q, codes, labels, lp)
        if template and template["yes_is"] == "no":
            a = {"type": "noul", "noul": round(1 - a["noul"], 4)}
        return a, j.get("usage", {}).get("prompt_tokens", 0)


def _fields(fmt: str) -> list[str]:
    import string
    return [f for _, f, _, _ in string.Formatter().parse(fmt) if f]


engine = Ollama()
