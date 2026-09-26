"""A hunch engine for local models served by Ollama (https://ollama.com), as a plugin.

`model: ollama:<name>` (e.g. `ollama:qwen2.5:0.5b`) asks the model through Ollama's OpenAI-compatible API and reads
the answer from the first answer token's probabilities: the same prompt and reading as hunch's built-in LLM
engine (`core.llm_prompt`, `core.llm_answer`), so a local model and a hosted one differ only in the model.
One request per question; local, so it costs nothing and runs one row at a time. OLLAMA_HOST moves the server.
"""
import asyncio
import os

import httpx

from hunch import core


class Ollama:
    adapter = "ollama-logprobs-v1"  # in every answer's key: change it when the prompt or the reading changes
    concurrency = 1  # one model on one machine: requests in parallel only queue inside Ollama

    def __init__(self):
        self.base = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")

    async def answer(self, model: str, state, questions: dict) -> dict:
        name = model.split(":", 1)[1]
        async with httpx.AsyncClient(timeout=300) as client:
            got = await asyncio.gather(*[self._one(client, name, q, state) for q in questions.values()])
        return {"answers": dict(zip(questions, (a for a, _ in got))), "tokens": sum(t for _, t in got), "cost": 0.0}

    async def _one(self, client, name: str, q: dict, state) -> tuple[dict, int]:
        prompt, codes, labels = core.llm_prompt(q, state)
        r = await client.post(f"{self.base}/v1/chat/completions", json={
            "model": name, "messages": [{"role": "user", "content": prompt}], "max_tokens": 8,
            "temperature": 1, "logprobs": True, "top_logprobs": 20})
        r.raise_for_status()
        j = r.json()
        lp = (j["choices"][0].get("logprobs") or {}).get("content") or []
        return core.llm_answer(q, codes, labels, lp), j.get("usage", {}).get("prompt_tokens", 0)


engine = Ollama()
