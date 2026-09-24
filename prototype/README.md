# prototype

Throwaway single-file prototype of hunch's core loop (see `docs/04-design.md`). Not the real architecture.

```sh
export TYPESAFE_AI_API_KEY=...        # or TYPESAFE_API_KEY
cd examples/banking77
uv run ../../hunch.py compile intent.yml                    # exact request payload + cost estimate
uv run ../../hunch.py run     intent.yml                    # ask what's missing, materialize table in .hunch/store.duckdb
uv run ../../hunch.py test    intent.yml [--source holdout.csv]   # accuracy, calibration, dial, confident mistakes, order stability
uv run ../../hunch.py diff    intent.yml --against git:HEAD [--source ...]   # flips, fixed/broken, sign test
uv run online_demo.py                                       # judge() from an app, same cache as batch
```

Spec fields `act` and `gold` are hunch-only: never sent to the engine, not part of the cache key. `tests:` per question: `min_accuracy`, `max_calibration_error`, `min_act_accuracy`, `order_stability: {sample, permutations, max_flip_rate}`.

## What it does

- **Content-addressed cache**: `key = sha256(model, state, question)` per (row, question), **order-preserving** (option order is model input). DuckDB.
- **Read once**: all uncached questions of a row (incl. permuted variants) go in one request.
- **test**: gold accuracy; expected calibration error + reliability table; the dial (automated % vs error among automated per threshold); most confident mistakes (dangerous, or gold errors); most confused pairs; option-order stability on a deterministic sample (reversed + seeded shuffles, answers cached too).
- **diff**: old spec (file or `git:REF`) on *today's* data; flips, ✓ fixed / ✗ broken, **paired sign test**, `~noise` flag near the boundary.
- **online**: `judge(spec, **fields)` / `ajudge` share keys with batch in both directions.

## Examples

- `examples/tickets/`: 40 hand-written support tickets. Too easy; useful as a smoke test only.
- `examples/banking77/`: 770-row dev + 385-row disjoint holdout from BANKING77 (77 intents, real messy queries, CC BY 4.0, see NOTICE). `intent.yml` is v3 (all 77 options described from the **train** split).

## Results

### tickets (2026-09-24, jev-1.13.0)

| Step | Calls | Cost | Result |
|---|---|---|---|
| First `run`, 40 × 3 questions | 40 requests, 1.8 s | $0.0007 | |
| Second `run` | 0 | $0 | 0.18 s |
| Add option descriptions, `diff` | 40 (department only) | $0.0007 | 1 flip (#21 fixed), 97.5% → 100%, **sign test p=1.0: not evidence** |
| Change only `act` | 0 | $0 | re-routes rows |
| Reword `urgent`, `diff` | 40 | $0.0005 | 10/40 flip, e.g. "Custom contract" → urgent (bad), visible before shipping |

### banking77 — three spec versions, scored on dev (770) and holdout (385)

| Version | Dev acc | Holdout acc | Holdout vs v1 (fixed/broken, p) | Holdout calib. error | Holdout automated @0.90 (error) | Holdout order flips |
|---|---|---|---|---|---|---|
| v1 bare labels | 78.6% | 82.3% | — | 0.066 | 68% (6.9%) | 6.7% |
| v2 29 confused intents described | 81.8% | 84.4% | 22/14, p=0.24 **n.s.** | 0.053 | 69% (6.0%) | 8.3% |
| v3 all 77 described | 85.5% | **88.3%** | 28/5, **p<0.001** | 0.053 | **81% (4.2%)** | **3.7%** |

v3 holdout dial: 0.99 → 65% automated at 2.0% error; 0.95 → 76% at 3.7%.

Order test on v1 dev: every flip had original p ≤ 0.74 → order effects live below any sensible `act`.

**Gold noise.** Adjudicated (by Claude, not a human) every mistake with p ≥ 0.99 on v1 dev (16) and p ≥ 0.97 on v3 holdout (7): 13 gold wrong, 10 ambiguous (dataset conventions conflict, e.g. near-identical train examples under different intents), **0 clearly Jev wrong**. Measured calibration error is partly gold error.

### online (banking77)

- Row already judged by batch → `judge()` cache hit, 9–20 ms (mostly opening DuckDB per call).
- New text → 790 ms (77-option request ≈ 2k tokens), then 9 ms; a later batch over it: 1 cached, $0.
- **While a batch holds the store, `judge()` fails** (`Could not set lock on file`): DuckDB is single-writer-process.
- SQLite WAL throwaway test (1 bulk writer + 2 online read/write loops, 3 s): 0 errors / ~47k ops, but worst single op up to 2.8 s under write contention. And switching to WAL must happen once at store creation (concurrent first opens raced: `database is locked`).

Total API spend for everything above: **$0.30** (4,826 answers).
