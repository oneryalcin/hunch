# prototype

Throwaway single-file prototype of hunch's core loop (see `docs/04-design.md`). Not the real architecture.

```sh
export TYPESAFE_AI_API_KEY=...        # or TYPESAFE_API_KEY
cd examples/banking77
uv run ../../hunch.py lint    intent.yml                    # spec checks (also run before every command)
uv run ../../hunch.py compile intent.yml                    # exact request payload + cost estimate
uv run ../../hunch.py run     intent.yml                    # ask what's missing, materialize table in .hunch/store.sqlite
uv run ../../hunch.py test    intent.yml [--source holdout.csv]   # accuracy, calibration, AUROC, dial, confident mistakes, order stability
uv run ../../hunch.py diff    intent.yml --against git:HEAD [--source ...]   # flips, fixed/broken, sign test
uv run ../../hunch.py review  intent.yml [--list] [--limit N]    # disputed + uncertain rows → <judgment>.reviews.csv
uv run online_demo.py                                       # judge() from an app, same store as batch
```

Spec fields `act` and `gold` are hunch-only: never sent to the engine, not part of the cache key. `tests:` per question: `min_accuracy`, `max_calibration_error`, `min_act_accuracy`, `min_auroc` (noul), `order_stability: {sample, permutations, max_flip_rate}` (choice).

## What it does

- **Content-addressed store**: `key = sha256(model, state, question)` per (row, question), order-preserving, line endings normalized in what is sent *and* hashed. SQLite in WAL mode: batch runs and apps share it. Each response is saved as it arrives, so a failure part-way loses nothing already paid for; retries honour `retry-after`.
- **Read once**: all uncached questions of a row (incl. permuted variants) go in one request.
- **lint**: unknown keys (typos), act range, missing columns, API limits, and *partially described choice options* (measured to hurt).
- **test**: accuracy; calibration error + reliability table; AUROC for yes/no; the dial (automated % vs error among automated); most confident mistakes; confusion pairs; option-order stability. With reviews, shows reviewed-gold and raw-gold numbers side by side.
- **diff**: old spec (file or `git:REF`) on *today's* data; flips, ✓ fixed / ✗ broken, paired sign test, `~noise` flag.
- **review**: queue of *disputed* rows (confident answer ≠ gold: a model error or a gold error) and *uncertain* rows (below `act`, no gold). Verdicts (`model_right`, `key_right`, `labeled`, `ambiguous`) append to `<judgment>.reviews.csv` next to the spec, tied to a hash of the row's text; they override gold in `test`/`diff`, `ambiguous` drops the row from scoring.
- **online**: `judge(spec, **fields)` / `ajudge` share keys with batch in both directions.

## Examples

- `examples/tickets/`: 40 hand-written support tickets. Smoke test only.
- `examples/banking77/`: 770-row dev + disjoint 385-row holdout from BANKING77 (77 intents, CC BY 4.0). `intent.yml` = v3 (all 77 options described from the train split). `intent.reviews.csv` = 13 verdicts on holdout disputes (reviewer: claude, not a human).
- `examples/swe_agent/`: 200 real SWE-agent trajectories (100 passed their tests, 100 failed; CC BY 4.0), built by `prepare.py`. `patch_eval.yml` asks: is it resolved (gold = tests passed)? does the agent claim it fixed it?

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
- Bug found by the panel: `prepare.py` clipped the end of long final messages (where the claim is); fixed to keep the tail, 17 rows changed, numbers above are after the fix.

### neutral review panel

Three context-free Claude subagents, blind protocol (`review_panel/README.md`): Jev **95.8%** acceptable (95% CI 88.7–97.9%) vs the answer key's 91.9% on the BANKING77 holdout; of 45 disagreements, key wrong 20, Jev wrong 5, both acceptable 20. Replaces the biased 91% upper bound above.

### online and the store

| Situation | Result |
|---|---|
| Row already judged by batch | 0.5–6 ms |
| New text | ~600 ms, then stored for everyone |
| **`judge()` while a real batch writes 200 answers** (SQLite WAL) | **63 hits, p50 0.6 ms, p99 3.0 ms, 0 errors** (DuckDB: crashed) |
| Same text, `\r\n` vs `\n` | was a miss (174/200 traces differ); fixed by normalizing line endings |

Total API spend for everything: **$0.37**.
