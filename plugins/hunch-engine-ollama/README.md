# hunch-engine-ollama

An engine plugin for [hunch](https://github.com/oneryalcin/hunch): any model Ollama serves becomes a hunch engine.

```yaml
model: ollama:qwen2.5:0.5b                        # hunch's own prompt, read from token probabilities
model: ollama:bespoke-minicheck#rag-unsupported   # the model's own format, from a template in this package
```

```sh
hunch install hunch-engine-ollama                  # once published; from a checkout: hunch install ./plugins/hunch-engine-ollama
hunch plugins                                      # ollama:  hunch-engine-ollama==0.1.0  ok
hunch test spec.yml --model ollama:qwen2.5:0.5b --max-cost 0 --receipt   # measured like any engine; costs nothing
```

It is also the reference plugin: one async `answer()`, answers in Jev's shapes, a cache `adapter` that changes with
any template, `concurrency = 1` for a single local model, and smoke tests that run without Ollama
(`python -m pytest`). See `hunch.engines` for the contract, and hunch's Engines reference for what it measured.

A template (`TEMPLATES` in `hunch_engine_ollama.py`) asks a specialised model in its own format and says what its Yes
means for the spec's question. The name after `#` is part of the model string, so its answers are cached and
benchmarked apart from the generic prompt's.
