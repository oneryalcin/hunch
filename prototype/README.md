# prototype

Throwaway single-file prototype of hunch's core loop (see `docs/04-design.md`). Not the real architecture.

```sh
export TYPESAFE_AI_API_KEY=...        # or TYPESAFE_API_KEY
cd examples/banking77
uv run ../../hunch.py lint    intent.yml                    # spec checks (also run before every command)
# any command also takes a folder of specs (a project): uv run hunch.py run recipes/agent_eval/
uv run ../../hunch.py compile intent.yml                    # exact request payload + cost estimate
uv run ../../hunch.py run     intent.yml                    # ask what's missing, materialize table in .hunch/store.sqlite
uv run ../../hunch.py test    intent.yml [--source holdout.csv]   # accuracy, calibration, AUROC, dial, confident mistakes, order stability
uv run ../../hunch.py diff    intent.yml --against git:HEAD [--source ...]   # flips, fixed/broken, sign test
uv run ../../hunch.py review  intent.yml [--list] [--limit N] [--audit N]   # disputed + audit + uncertain rows → <judgment>.reviews.csv
uv run online_demo.py                                       # judge() from an app, same store as batch
uv run shadow_demo.py                                       # judge(live, shadow=candidate) + diff/review --traffic
# judge(spec, log=True, **row) keeps rows so a candidate written later can be replayed with --traffic
```

Sources: `source:` is a CSV, `traces(<glob>)` (Claude Code, Cursor, OpenCode, OpenTelemetry GenAI sessions; `view: turns | runs`; see `traces.py`) or `py(<file.py>:<function>)` (any function returning dicts, e.g. a dlt resource). `redact: [secrets, emails, home, <regex>]` and `clip: {column: N | -N}` apply before anything is hashed or sent. Question extras: `none: "<when>"` (a none-of-these option), `type: multi` (several options can apply; one yes/no each), `escalate: {model: <engine>}` (re-ask answers below `act` on another engine). `examples/features/` shows each.

Your own Claude Code sessions: `source: traces(~/.claude/projects/*dev-personal*/*.jsonl)` with `redact: [secrets, emails, home]`.

Engines: `model: jev-1.13.0` (TypeSafe), or an LLM through its answer-token logprobs: `deepseek:<id>` (DeepSeek API, `DEEPSEEK_API_KEY`) or `openrouter:<id>[@provider]` (`OPENROUTER_API_KEY`). `--model X` on any command runs the same specs on another engine (tables get a `__<engine>` suffix); `hunch diff SPEC --against SPEC --model X` compares engines row by row.

Spec fields `act` and `gold` are hunch-only: never sent to the engine, not part of the cache key. `act` is a number, or `{yes: .., no: ..}` on yes/no questions. `tests:` per question: `min_accuracy`, `max_calibration_error`, `min_act_accuracy`, `min_auroc` (noul), `base_rate` (noul: score as if this share of rows were yes), `order_stability: {sample, permutations, max_flip_rate}` (choice).

## What it does

- **Content-addressed store**: `key = sha256(model, state, question)` per (row, question), order-preserving, line endings normalized in what is sent *and* hashed. SQLite in WAL mode: batch runs and apps share it. Each response is saved as it arrives, so a failure part-way loses nothing already paid for; retries honour `retry-after`.
- **Read once**: all uncached questions of a row (incl. permuted variants) go in one request.
- **lint**: unknown keys (typos), act range, missing columns, API limits, and *partially described choice options* (measured to hurt).
- **test**: accuracy; calibration error + reliability table; AUROC for yes/no; the dial (automated % vs error among automated); most confident mistakes; confusion pairs; option-order stability. With reviews, shows reviewed-gold and raw-gold numbers side by side.
- **diff**: old spec (file or `git:REF`) on *today's* data; flips, ✓ fixed / ✗ broken, paired sign test, `~noise` flag.
- **review**: queue of *disputed* rows (model ≠ answer key), *audit* rows (a fixed random sample where they agree) and *uncertain* rows (below `act`, no gold). Verdicts (`model_right`, `key_right`, `both_ok`, `confirmed`, `labeled`, `ambiguous`) append to `<judgment>.reviews.csv` next to the spec, tied to a hash of the row's text. Gold is a set of acceptable labels. With both disagreements and an audit reviewed, `test` headlines a stratified accuracy estimate with a 95% CI.
- **online**: `judge(spec, **fields)` / `ajudge` share keys with batch in both directions.

## Projects (Phase 2)

A folder of specs is a project. A judgment can read another's output with `source: ref(name)` (every input column plus that judgment's answers), keep only some rows with `where: "claim == 'claimed_fixed'"`, and `union: [a, b]` merges branches. `weights: {by, population}` on the judgment that reads the file declares how the rows were sampled; each row's weight flows downstream. `chain: true` on a refinement (its answer can only be right if the row was routed correctly) multiplies its confidence by P(its where-clause holds) under the upstream answer's distribution; filters that only decide whether to ask are not chained. Reviews record their `kind` (audit / disputed / uncertain); only audits stand in for unreviewed agreeing rows. One store per workspace (`prototype/.hunch/`). Every fill prints its estimated cost; `--max-cost` refuses above a cap.

- `recipes/agent_eval/`: the first recipe (see its README).
- `examples/banking77_tree/`: coarse → fine experiment, generated by `build.py`.

## Examples

- `examples/tickets/`: 40 hand-written support tickets. Smoke test only.
- `examples/banking77/`: 770-row dev + disjoint 385-row holdout from BANKING77 (77 intents, CC BY 4.0). `intent.yml` = v3 (all 77 options described from the train split). `intent.reviews.csv` = 13 verdicts on holdout disputes (reviewer: claude, not a human).
- `examples/swe_agent/`: 200 real SWE-agent trajectories (100 passed their tests, 100 failed; CC BY 4.0), built by `prepare.py`. `patch_eval.yml` asks: is it resolved (gold = tests passed)? does the agent claim it fixed it? `patch_only.yml` is the same question without the agent's messages (an ablation).
- `examples/claude_code/`: 309 turns of real developer ↔ Claude Code sessions (Trace Commons, CC BY 4.0), read directly with `source: traces(...)` (download command in NOTICE.md; the sessions stay in the gitignored `.cache/`). `outcome.yml`: how did the turn go, from the developer's next message? `claims.yml`: does the agent say it's done?

## Results (jev-1.13.0, 2026-09-24)

### tickets

| Step | Calls | Cost | Result |
|---|---|---|---|
| First `run`, 40 × 3 questions | 40 requests, 1.8 s | $0.0007 | |
| Second `run` | 0 | $0 | 0.18 s |
| Add option descriptions, `diff` | 40 | $0.0007 | 1 flip fixed; sign test p=1.0: not evidence |
| Reword `urgent`, `diff` | 40 | $0.0005 | 10/40 flip, e.g. "Custom contract" → urgent, visible before shipping |

### banking77: three spec versions, dev (770) and holdout (385)

| Version | Dev acc | Holdout acc | Holdout vs v1 (fixed/broken, p) | Holdout calib. error | Holdout automated @0.90 (error) | Order flips |
|---|---|---|---|---|---|---|
| v1 bare labels | 78.6% | 82.3% | — | 0.066 | 68% (6.9%) | 6.7% |
| v2 29 of 77 described | 81.8% | 84.4% | 22/14, p=0.24 n.s. | 0.053 | 69% (6.0%) | 8.3% |
| v3 all 77 described | 85.5% | **88.3%** | 28/5, **p<0.001** | 0.053 | **81% (4.2%)** | **3.7%** |

**Review of the 13 holdout disputes** (v3, act 0.90): 4 model right, 2 answer key right, 7 ambiguous.

| v3 holdout | raw gold | reviewed gold |
|---|---|---|
| accuracy | 88.3% | 91.0% |
| calibration error | 0.053 | 0.030 |
| top bin stated → observed | 0.990 → 0.958 | 0.991 → 0.993 |
| accuracy among auto-acted @0.90 | 95.8% (81% of rows) | 99.3% (80%) |

Caveat: only disputes were reviewed (rows where the model disagreed). Rows where a wrong gold label *agrees* with the model are never checked, and dropping ambiguous rows only among disputes flatters accuracy. Treat 91.0% as an upper bound; the real queue needs a random audit slice.

### swe_agent: judging real agent runs (200 traces, balanced 100/100)

- `resolved` (does the patch pass the hidden tests?): **AUROC 0.831**, accuracy 73.5%. Calibration error 0.114, partly an artifact of the 50/50 sample (real pass rate ≈17%).
- Asymmetric: when p(resolved) < 0.2, **52 of 55** actually failed. Yes/no questions need separate yes and no thresholds.
- Overclaiming: agents claimed a fix in 160/200 runs; **66 of those (41%) failed** the tests. Jev put 23 of the 66 below p=0.2. `claims_fixed` was checked by a blind 3-reviewer panel: Jev matches the reviewer majority on 29/30 decided rows (see `review_panel/`).
- Does the agent's claim sway the judge? No: without `final_messages` AUROC is 0.828 (vs 0.831), and a claim doesn't raise p(passes) (`hunch diff examples/swe_agent/patch_only.yml --against examples/swe_agent/patch_eval.yml`; docs/04-design.md round 7).
- Bug found by the panel: `prepare.py` clipped the end of long final messages (where the claim is); fixed to keep the tail, 17 rows changed, numbers above are after the fix.

### Phase 1 (trust the numbers)

- BANKING77 holdout with the panel's 105 verdicts imported (`review_panel/to_reviews.py`): `test` headlines **estimated accuracy 95.8% (95% CI 88.7–97.9%)**, matching the panel's own scorer; current-gold accuracy (98.2%) and raw-key accuracy (88.3%) shown as secondary.
- SWE `resolved` at the real pass rate (16.7%, `base_rate`): Jev is overconfident on "yes"; two-sided dial with `act: {yes: 0.90, no: 0.80}`: acting on "no" automates 44% of runs at 1.9% error, acting on "yes" is never safe.

### neutral review panel

Three context-free Claude subagents, blind protocol (`review_panel/README.md`): Jev **95.8%** acceptable (95% CI 88.7–97.9%) vs the answer key's 91.9% on the BANKING77 holdout; of 45 disagreements, key wrong 20, Jev wrong 5, both acceptable 20. Replaces the biased 91% upper bound above.

### online and the store

| Situation | Result |
|---|---|
| Row already judged by batch | 0.5–6 ms |
| New text | ~600 ms, then stored for everyone |
| **`judge()` while a real batch writes 200 answers** (SQLite WAL) | **63 hits, p50 0.6 ms, p99 3.0 ms, 0 errors** (DuckDB: crashed) |
| Same text, `\r\n` vs `\n` | was a miss (174/200 traces differ); fixed by normalizing line endings |

### shadow mode

`judge(LIVE, shadow=CANDIDATE, **row)` returns the live answer; the candidate answers the same row (cached, never returned) and the row is logged in the store. `--traffic` makes any command read those logged rows as its source:

```
hunch diff   CANDIDATE --against LIVE --traffic   # where would the candidate have answered differently? ($0: all cached)
hunch review CANDIDATE --against LIVE --traffic   # review only those rows (kind=shadow) → diff then says which side wins
```

`examples/banking77/shadow_demo.py`: live = flat intent spec, candidate = the tree, 40 customer messages as traffic; the candidate differs on 8, $0.

### claude_code: real conversations (309 turns)

- `outcome` 90% and `claims_done` 88% against a blind 3-reviewer panel (60 random turns; `review_panel/claude_code/`). At act = 0.80, `outcome` labels 77% of turns at 2.2% error.
- Claimed done after editing: the developer reports a problem in 33% of turns where the agent ran nothing after its last edit vs 16% where it ran a command (142 vs 31 turns; session bootstrap CI −1 to +30 points: suggestive).
- Cost $0.020. Details: docs/04-design.md round 9.

### second engine, suggest, scale (docs/04-design.md rounds 10, 12, 14)

- Same specs on DeepSeek V4.1 Flash (answer-token logprobs): BANKING77 92.3% vs Jev 95.8% (paired p = 0.001, Jev better); SWE patch AUROC 0.866 vs 0.831 and Claude Code outcome/claims level (n.s.); ~1.5× Jev's cost as billed.
- `hunch suggest` on a bare-label BANKING77 spec: a rewrite gained +5.1% on the held-out half (p = 0.008) but +2.4% (n.s.) on the untouched holdout; now Bonferroni-corrected, and confirming on a holdout is part of the workflow.
- 100,000 Amazon reviews: 57.5 requests/s, no rate limiting, 0.03% retried, $1.55; accuracy 97.0%, AUROC 0.993; a cached re-run takes 4 s.

### Phase 2 (the graph)

- BANKING77 tree vs flat (holdout): flat 95.8% vs tree 87.5% estimated accuracy (all 69 tree disagreements reviewed blind); tree fixed 3, broke 35 (p < 0.001); tree 48% cheaper; 33 rows routed to a group without their answer. Chained confidence (`chain: true`) separates right from wrong answers with AUROC 0.897 vs 0.796 own-only.
- agent_eval on 200 SWE-agent runs: 149 claim a fix; auto-reject at 0.80 handles 23.3% of claimed runs at 3.4% error (0.90: 9.3% at 0%); verified-before-claim runs pass 28.9% vs 17.8% (suggestive, p = 0.079).
- Adversarial review of Phase 2 (Fable): 14 findings, all verified; fixes and corrections in `docs/04-design.md` round 6.

Total API spend for everything: **about $2.76** (the 100k-row scale test was $1.55 of it), of which ~$0.08 was accidental re-asking in Phase 2 (per-folder stores, a spec outside the workspace; see docs/04-design.md rounds 6–7).
