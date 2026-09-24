# 06 — Roadmap (draft, 2026-09-24)

Ordered by risk: the things we don't know yet come before the things we only have to build. Each item names the question it answers and how we'll know. Costs are API spend at jev-1.13.0 prices.

## Product principles

These decide between options at every phase. They come from why dbt and dlt won: not one feature, but small pieces that compose into recipes people share.

1. **Composable, not a single question.** A few primitives (source, judgment, test, route, review, sink) snap together into recipes. Judgments `ref()` sources, other judgments, and dbt models. No feature may exist only for one use case.
2. **Recipes are the unit of reuse.** A recipe is a folder of specs, tests and gold that someone else can install and adapt, like a dbt package or a dlt verified source. The first one is an agent-eval recipe for our first user.
3. **Lineage everywhere.** Every answer can say which spec version, model, run, and upstream answers produced it.
4. **Don't build connectors.** Data comes in through dlt (any dlt resource is a hunch source) or dbt (any model is a hunch source). hunch's job starts at "rows exist".
5. **Library first, then CLI, then server.** Import it anywhere (notebook, app, CI, agent); the CLI is a thin shell; the server is optional.
6. **The first ten minutes decide adoption.** `hunch init <recipe>` → a working example with sample data → `run`, `test`, `diff` on your own rows. Errors say what to do.
7. **Built for coding agents too.** Specs are plain files; `lint → test → diff` is the feedback loop an agent uses to improve them.

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

## Phase 1: trust the numbers: DONE 2026-09-24 ($0 new API spend)

| Item | Result |
|---|---|
| Random audit slice | `review --audit N` mixes a fixed random sample of agreeing rows into the queue; `test` reports a stratified estimate with a 95% CI and refuses to estimate from disputes alone. Reproduces the panel's 95.8% (88.7–97.9%) exactly. |
| Asymmetric yes/no thresholds | `act: {yes, no}` + two-sided dial. Agent evals: auto-reject 44% of runs at 1.9% error; auto-accept is never safe. |
| Calibration at the production base rate | `base_rate` reweights; SWE at 16.7% shows Jev **over**confident on "yes" (the 50/50 sample made it look under). |
| Gold as acceptable sets | `both_ok` verdicts; 20 of 45 BANKING77 disagreements. |

Also found: YAML's Norway problem (yes/no parsed as booleans), fixed in the spec loader. Open: calibration and auto-acted accuracy under review-corrected gold still lean upward; per-class base rates for choice questions.

## Phase 2: the graph and the first recipes (prototype, the big unknown, ~$0.10–0.30)

| Item | Question it answers | Done when |
|---|---|---|
| `ref()` between judgments | Does chaining judgments work as a first-class idea? | A judgment's `source` or `state` can reference another judgment's output; `run` orders the DAG; the cache still keys each node by its exact input. |
| Experiment: hierarchical vs flat on BANKING77 | Does coarse → fine (e.g. 10 groups, then only that group's intents) beat one 77-way question on accuracy, calibration, or cost? | Holdout numbers for both, with a sign test. Either answer is useful: it tells us whether the graph earns its complexity. |
| Diff across a graph | When an upstream wording changes, which downstream labels move? | `diff` reports flips per node, including nodes whose own spec didn't change. |
| Conditional nodes | Can a node run only where upstream said yes (e.g. judge fix quality only where `claims_fixed`)? | Cost drops in proportion to the filter, and the diff still holds. |
| First recipe: agent-eval | Do the primitives compose into something a user would install? | One folder: trace source → `claimed_done` → `verified_before_done` / `fix_correct`, plus `asked_needless_question`; tests, gold, review, lineage. Runs on the SWE-agent traces and on Claude Code session logs. |
| Generality check: a second, unrelated recipe | Are the primitives general, or shaped around agent evals? | A support-ticket triage recipe (coarse → fine, from the BANKING77 experiment) built with **zero** new special-case code. Any special case found = redesign the primitive, not patch it. |

## Phase 3: engine independence and scale (prototype, ~$0.50–2)

| Item | Question it answers | Done when |
|---|---|---|
| Second engine adapter | Is the spec really engine-neutral? | Same spec runs on an LLM with logprobs (or an open System One clone); swapping is one string. |
| Cross-engine diff | Which engine should this judgment use? | `diff --against engine:X` shows flips, accuracy, calibration, cost per engine. |
| Scale test at 100k rows | Where does it break: hashing, store size, rate limits? | Measured throughput. Known constraint: 1,200 requests/min means 1M rows ≈ 14 h on one key. Decide batching / multi-key / cursor from data. |
| Cursor + hash incremental | Can we avoid re-reading an unchanged big table? | `incremental: {cursor: updated_at}` skips old rows; hash still decides whether to ask. |
| Trace source via dlt (moved up for the AI-engineer persona) | Can hunch read agent traces directly instead of a hand-built CSV? | Any dlt resource is a hunch source; demonstrated with an OpenTelemetry GenAI / Langfuse-style export. Per-field trimming is declared in the spec (keep the head of an issue, the tail of a conversation), not hand-coded like `prepare.py`. |

## Phase 4: package v0.1 (product, Apache 2.0)

Known engineering; do it once phases 1–3 have settled the shapes.

- **Library first**: `hunch.run()`, `hunch.judge()`, `hunch.results("x").df()`; CLI is a thin shell.
- **Spec format**: JSON Schema, versioned; lint built on it.
- **Lineage**: `_hunch_run_id` and `_hunch_key` on every materialized row; `_hunch_runs` table (spec hash, git sha, model, cost, status). Downstream reads complete runs only.
- **Spec-change policy**: `on_change: reask | new_rows_only | freeze`; `diff` shows the cost before you pay it.
- **Stores**: SQLite (local), Postgres (shared), selected by one string.
- **dbt interop**: read `manifest.json` as sources; emit dbt `sources:` YAML for materialized judgments.
- **Agent skill / MCP**: coding agents write specs and iterate with lint → test → diff.
- **Recipes as packages**: `hunch init agent-eval`, `hunch add <recipe>`; a recipe hub later. Recipes are versioned, have their own tests and gold, and are overridable (change a threshold or a question without forking).
- **UX pass**: first-ten-minutes path timed with a new user; every error message says what to do next; a Python API (decorators, like dlt) that produces the same spec as the YAML.
- **dlt interop**: dlt resources as sources; hunch results loadable by dlt to any destination.
- **Run-level checks (built in, need run history)**: label-distribution drift vs the previous run, review-rate ceiling, confidence drift, cost budget per run. Generic checks on the output table (nulls, accepted values, ranges) are *not* built: documented as Great Expectations / Soda / dbt tests pointed at hunch's table.

## Ideas borrowed from Great Expectations (UX, not integration)

GX is a reference for what makes quality checks *useful to people*, not a dependency. Decided 2026-09-24: no GX integration unless a user needs it. What to borrow:

- **Named, readable checks.** GX's "expect_column_values_to_be_between" reads like a sentence a non-engineer can review. hunch tests should read the same way in the spec and in reports ("expect accuracy ≥ 90% on the holdout", "expect ≤ 15% sent to review").
- **Docs generated from results.** GX's Data Docs turn every validation into a browsable report. hunch equivalent: `hunch report` writes one shareable page per run (dial, calibration, confident mistakes, diff, lineage), like the field-report artifact but generated.
- **Checkpoints.** A named bundle of "which data, which checks, what to do on failure" (fail CI, notify, open review queue). hunch equivalent: a `checks:` block per recipe that CI and schedules run.
- **Profiling to get started.** GX can propose checks from a sample. hunch equivalent: `hunch suggest` proposes thresholds from the dial, flags bare options, and drafts option descriptions from example rows, so a new user starts from a reasonable spec.
- **A gallery of checks.** Browsable, documented, reusable. hunch equivalent: the recipe hub, with tests included.

Parked: a GX custom expectation / dbt generic test backed by hunch (`expect_column_values_to_satisfy("company_name", "is a real company")`). Revisit only if users ask for it.
- **Release**: PyPI name decided (`hunch` is a squatted placeholder), repo public, docs site.

## Phase 5: server (ELv2)

Only after v0.1 has users.

- Review UI (the queue, audit slice, "answer key is wrong"), multi-reviewer, agreement stats.
- Online serving with a shared store and background writes.
- Live trace ingestion for evals on production traffic (the batch trace source lands in Phase 3).
- Monitoring: label drift, calibration drift, cost per judgment over time.

## Parked (revisit with evidence)

- Score-question calibration (ordinal); `suggest-rules` (demote a judgment to a rule when a rule agrees on ≥X% of gold); multi-reviewer consensus; per-row noise estimates by resampling.
