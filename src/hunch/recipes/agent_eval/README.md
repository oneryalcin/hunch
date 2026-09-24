# agent-eval

Check a coding agent's work from its trace, without running anything: does it claim a fix, would the fix pass, did it check first.

```
runs (one row per agent run)
  └─► claims        what does the agent say?   claimed_fixed / no_claim / unclear
        └─ where claim == 'claimed_fixed' ─► fix_correct   would the patch pass the project's tests?   (gold: resolved)
        └─ where claim == 'claimed_fixed' ─► verified      did the agent check before claiming?        (no gold yet)
```

Your rows (`runs.csv`, or any source: `traces(<glob>)`, `py(file.py:fn)`) need:

| column | what |
|---|---|
| `id` | one per run |
| `final_messages` | the agent's last messages, **tail-trimmed** (the claim is at the end; `clip: {final_messages: -4000}`) |
| `issue` | the task it was given |
| `patch` | the diff it produced |
| `resolved` | optional gold: `yes`/`no`, did the project's tests pass |

```sh
hunch compile .           # what it will cost (fix_correct/verified: an estimate until claims are answered)
hunch run .    --max-cost 1
hunch review . --node verified   # build gold where there is none
hunch test .
```

Measured on 200 SWE-agent runs (hunch repo, `prototype/recipes/agent_eval`): claim detection 97% against a blind review panel; auto-rejecting runs when p(fails) ≥ 0.80 handled 23% of claimed runs at 3.4% error. Set `weights` in `claims.yml` if your sample's pass rate differs from production's.
