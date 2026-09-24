# 06 — Roadmap (draft, 2026-09-24)

Ordered by risk: the things we don't know yet come before the things we only have to build. Each item names the question it answers and how we'll know. Costs are API spend at jev-1.13.0 prices.

## Where we are

Proven in the prototype (see `prototype/README.md`): content-addressed store shared by batch and online, lint, calibration / AUROC / dial / order tests, backtest diff with a significance test, review queue with gold correction. Validated on intent classification (BANKING77) and agent-run evals (SWE-agent). Total spend so far: $0.37.

**Not proven at all:** the dependency graph between judgments (`ref()`), the core of the dbt analogy. Also untested: a second engine, scale beyond 1k rows, and whether anyone besides us wants this.

## Phase 0: decide who it's for (no code)

| Question | How we'll know |
|---|---|
| Who is the first user: analytics engineer (labels in the warehouse), AI engineer (evals over traces), or ops (routing rules)? | One named persona, one real workload they'd run weekly, written down. |
| What is that workload? | A dataset we can use, ideally with some gold labels, and a person who would review the queue. |

Everything below gets prioritized against that workload.

**Decided 2026-09-24: the AI engineer comes first** (evals over agent and LLM traces), then analytics engineers, then ops. Why: the SWE-agent result (39% of claimed fixes fail; confident "no" is 51/53 right) is the strongest evidence so far, and that audience feels the pain now. Still open: the named real workload and a reviewer.

Consequences for the phases below: trace ingestion (OpenTelemetry GenAI conventions) moves up from Phase 5 into Phase 3 as a batch source; asymmetric yes/no thresholds (Phase 1) and conditional nodes (Phase 2) matter most, because agent evals are mostly yes/no checks chained on earlier answers.

## Phase 1: trust the numbers (prototype, ~1 session, <$0.10)

Finishes the measurement story. Each item fixes a known way our numbers can mislead.

| Item | Question it answers | Done when |
|---|---|---|
| Random audit slice in the review queue | What is the gold error rate where the model *agrees* with gold? | `review` mixes N random agreed rows into the queue; `test` reports accuracy with a confidence interval instead of the disputes-only upper bound. |
| Asymmetric thresholds for yes/no (`act_yes`, `act_no`) | Can we automate the reliable side only? (SWE: p < 0.2 → 51/53 failed) | Dial shows automation and error separately for each side; SWE example automates confident "no" at < 5% error. |
| Calibration at the production base rate | Is Jev under- or over-confident on the real population? | `test` accepts a base rate (or sample weights) and reweights; SWE example re-scored at ~17%. |

## Phase 2: the graph (prototype, the big unknown, ~$0.10–0.30)

| Item | Question it answers | Done when |
|---|---|---|
| `ref()` between judgments | Does chaining judgments work as a first-class idea? | A judgment's `source` or `state` can reference another judgment's output; `run` orders the DAG; the cache still keys each node by its exact input. |
| Experiment: hierarchical vs flat on BANKING77 | Does coarse → fine (e.g. 10 groups, then only that group's intents) beat one 77-way question on accuracy, calibration, or cost? | Holdout numbers for both, with a sign test. Either answer is useful: it tells us whether the graph earns its complexity. |
| Diff across a graph | When an upstream wording changes, which downstream labels move? | `diff` reports flips per node, including nodes whose own spec didn't change. |
| Conditional nodes | Can a node run only where upstream said yes (e.g. judge fix quality only where `claims_fixed`)? | Cost drops in proportion to the filter, and the diff still holds. |

## Phase 3: engine independence and scale (prototype, ~$0.50–2)

| Item | Question it answers | Done when |
|---|---|---|
| Second engine adapter | Is the spec really engine-neutral? | Same spec runs on an LLM with logprobs (or an open System One clone); swapping is one string. |
| Cross-engine diff | Which engine should this judgment use? | `diff --against engine:X` shows flips, accuracy, calibration, cost per engine. |
| Scale test at 100k rows | Where does it break: hashing, store size, rate limits? | Measured throughput. Known constraint: 1,200 requests/min means 1M rows ≈ 14 h on one key. Decide batching / multi-key / cursor from data. |
| Cursor + hash incremental | Can we avoid re-reading an unchanged big table? | `incremental: {cursor: updated_at}` skips old rows; hash still decides whether to ask. |
| Trace source (moved up for the AI-engineer persona) | Can hunch read agent traces directly instead of a hand-built CSV? | OpenTelemetry GenAI spans (or a common trace export) load as rows, with state fields picked per span or per session. |

## Phase 4: package v0.1 (product, Apache 2.0)

Known engineering; do it once phases 1–3 have settled the shapes.

- **Library first**: `hunch.run()`, `hunch.judge()`, `hunch.results("x").df()`; CLI is a thin shell.
- **Spec format**: JSON Schema, versioned; lint built on it.
- **Lineage**: `_hunch_run_id` and `_hunch_key` on every materialized row; `_hunch_runs` table (spec hash, git sha, model, cost, status). Downstream reads complete runs only.
- **Spec-change policy**: `on_change: reask | new_rows_only | freeze`; `diff` shows the cost before you pay it.
- **Stores**: SQLite (local), Postgres (shared), selected by one string.
- **dbt interop**: read `manifest.json` as sources; emit dbt `sources:` YAML for materialized judgments.
- **Agent skill / MCP**: coding agents write specs and iterate with lint → test → diff.
- **Release**: PyPI name decided (`hunch` is a squatted placeholder), repo public, docs site.

## Phase 5: server (ELv2)

Only after v0.1 has users.

- Review UI (the queue, audit slice, "answer key is wrong"), multi-reviewer, agreement stats.
- Online serving with a shared store and background writes.
- Live trace ingestion for evals on production traffic (the batch trace source lands in Phase 3).
- Monitoring: label drift, calibration drift, cost per judgment over time.

## Parked (revisit with evidence)

- Score-question calibration (ordinal); `suggest-rules` (demote a judgment to a rule when a rule agrees on ≥X% of gold); multi-reviewer consensus; per-row noise estimates by resampling.
