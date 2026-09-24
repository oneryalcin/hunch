# recipe: agent_eval

Checks the work of a coding agent from its trace, without running anything. The first hunch recipe.

```
traces (one row per agent run)
  └─► claims        what does the agent say?   claimed_fixed / no_claim / unclear
        └─ where claim == 'claimed_fixed' ─► fix_correct   would the patch pass the project's tests?   (gold: tests)
        └─ where claim == 'claimed_fixed' ─► verified      did the agent check before claiming?        (no gold yet)
```

```sh
uv run ../../hunch.py compile agent_eval/      # what it will cost (upper bound until claims are answered)
uv run ../../hunch.py run     agent_eval/ --max-cost 0.05
uv run ../../hunch.py test    agent_eval/
uv run ../../hunch.py review  agent_eval/ --node verified   # build gold for the unverified judgment
```

To use your own traces: point `claims.yml`'s `source` at a CSV with `id`, `final_messages` (the agent's last messages, **tail-trimmed**: the claim is at the end), `issue`, `patch`, and, if you have it, a test outcome column for gold. Set `weights` if your sample over-represents passes or failures.

## What it does with the graph

- **Conditional judgments**: `fix_correct` and `verified` only run on runs that claim success (149 of 200 here), so the rest cost nothing.
- **Not chained**: `claims` only decides *whether* `fix_correct` asks; whether a patch passes its tests doesn't depend on what the agent claimed, so `fix_correct` acts on its own confidence (`chain: true` is for refinements, like the BANKING77 tree).
- **Weights flow downstream**: the source is a 50/50 sample of a population where 16.7% of runs pass. Among runs that *claim* a fix the real pass rate is ~24%, and `fix_correct` is scored against that automatically.
- **Diff across the graph**: change `claims` and `hunch diff` shows which runs newly reach or leave `fix_correct` and `verified`, even though their own specs are unchanged.

## Results on 200 SWE-agent runs (jev-1.13.0, 2026-09-24)

| | |
|---|---|
| claims | 149 claimed_fixed, 47 no_claim, 4 unclear |
| fix_correct (claimed runs only) | AUROC 0.758; auto-reject when "fails" ≥ 0.80: 23.3% of claimed runs at 3.4% error; ≥ 0.90: 9.3% at 0% |
| verified vs actual pass (population mix) | verified 28.9% pass, not verified 17.8% (n = 97 / 52; Fisher p = 0.079, suggestive only; `verified` itself is unvalidated) |
| cost | $0.009 (fix_correct was free: same question and text as `examples/swe_agent/patch_eval.yml`, already in the store) |
