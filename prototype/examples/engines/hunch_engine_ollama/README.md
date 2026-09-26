# hunch-engine-ollama

An example engine plugin: any model Ollama serves becomes a hunch engine, `model: ollama:<name>`.

```sh
uv pip install -e prototype/examples/engines/hunch_engine_ollama   # into the environment hunch runs in
hunch test spec.yml --model ollama:qwen2.5:0.5b --max-cost 0        # measured like any engine; costs nothing
```

It is about 40 lines: `answer()` builds hunch's own prompt, asks Ollama's OpenAI-compatible API for token
probabilities, and reads the answer the way hunch's built-in LLM engine does. See `hunch.engines` for the contract.
