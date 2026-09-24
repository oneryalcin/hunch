# 04 — Design

Status: sketch. Nothing here is built or validated yet.

## Decision: clean slate, not on dbt

Judgment workloads differ from SQL transforms at the core. Bending dbt would mean fighting it.

| Judgments need | dbt assumes |
|---|---|
| Network call per row: batching, rate limits, retries, partial failure, cost budgets | One SQL statement the warehouse runs |
| Per-row cache and provenance (which question version produced this label) | Model = a table; no row state |
| Human in the loop: review queue with write-back | No concept |
| Statistical tests (calibration, order stability) on gold sets | Tests = failing rows |
| Backtest across question versions | No history of past outputs |
| Sources beyond the warehouse: traces, files, APIs, streams | Warehouse tables |
| **Same definition online (per request in the app) and batch** | Batch only |

The last row alone justifies a new tool: define a judgment once, run it inside an app request **and** as a backfill.

**Cost of going standalone:** lose dbt's distribution; rebuild DAG, selectors, envs, docs. **Mitigation: interop, not dependency** (below).

## Core primitive: content-addressed judgments

Every result keyed by:

```
key = hash(rendered_state + question + options + engine + engine_version)
```

Everything falls out of this, like everything falls out of `ref()`:

| Feature | How |
|---|---|
| Incremental runs | Only rows whose key changed cost money |
| Backtest diff | Two question versions → two key sets → join → exactly which rows flipped |
| Reproducibility / audit | Any label traces to exact inputs |
| Online serving | Repeated inputs served from cache (cf. Postgres `jev()` 6 ms repeat) |
| Review write-back | Human answer stored against the same key → gold data |

Open: does the key include the rendered state text, or a hash of source row + template? (Rendered text is simpler and more honest; template changes should invalidate.)

## Concepts

| Concept | Meaning |
|---|---|
| **source** | Where rows come from: CSV/Parquet, DuckDB/warehouse query, dbt model (via manifest), trace store (OTel), API |
| **judgment** | state template over a source/ref + one or more questions. Materializes to a table |
| **question** | `choice` (options), `score` (scale with anchors), `noul` (statement) |
| **threshold / route** | Per question: act ≥ t_high, escalate between, maybe reject ≤ t_low |
| **review queue** | Materialization of escalated rows; answers write back by key |
| **gold set** | Seeds of labelled rows (hand-made or from reviews) |
| **test** | Assertions over a judgment (see below) |
| **engine** | Pluggable backend: Jev first; LLM logprob fallback |
| **store** | Content-addressed result store (DuckDB/Parquet locally) |

## Spec sketch (illustrative, not final)

```yaml
# judgments/ticket_triage.yml
judgment: ticket_triage
source: ref('support_tickets')           # a source, another judgment, or a dbt model
state: |
  Subject: {{ subject }}
  Body: {{ body }}
questions:
  department:
    choice: [billing, technical, sales]
    route: { act: 0.80, escalate: 0.35 }
  frustration:
    score:
      0: calm
      1: frustrated but civil
      2: very angry
  urgent:
    noul: "The message conveys urgency."
tests:
  - gold: seeds/ticket_triage_gold.csv
    accuracy: { min: 0.90 }
    calibration_error: { max: 0.08 }
  - order_stability: { question: department, permutations: 3, max_flip_rate: 0.02 }
  - consistency: { pair: [urgent, not_urgent], sum_within: 0.15 }
```

Online, same spec:

```python
from hunch import judge
r = judge("ticket_triage", subject=s, body=b)   # cache → engine; same key space as batch
if r.department.route == "act": ...
```

## Tests (derived from engine failure modes, see 01-jev limits)

| Test | Catches |
|---|---|
| gold accuracy | wrong answers vs labelled rows |
| calibration error (ECE) | confidence that can't be trusted for routing |
| order stability | option-order sensitivity (0.43 → 0.63) |
| consistency | complementary questions not summing ≈1 (0.72 + 0.47) |
| drift | distribution shift in labels/confidence between runs |
| classic row tests | `not_null`, `accepted_values` on outputs |

## CLI sketch

- `hunch compile` — render exact engine payloads (legibility, like dbt compiled SQL); cost estimate.
- `hunch run [-s selector]` — dbt-style selectors (`+model`).
- `hunch test`
- `hunch diff --against main` — backtest: rows that flip + samples. The CI hero feature.
- `hunch review` — local review of the queue (TUI or minimal web).

## Architecture

- **The spec is the product.** Language-neutral YAML; SDKs thin (Python first, TS later). A spec that becomes a standard is the durable asset.
- **Core:** compiler + graph + executor (async, batching, retries, budget) + content-addressed store.
- **Engines pluggable.** Jev first. Single proprietary vendor = adoption risk (pricing, uptime, enterprise questions); ~31 open copies exist.
- **Language: Python.** Bottleneck is network I/O, not CPU; Rust buys nothing (Fusion needed Rust for SQL parsing, we don't). Data people live in Python.
- **Local-first:** DuckDB/Parquet store; warehouse write-back adapters later.

## dbt interop (not dependency)

- Read `manifest.json` → dbt models usable as sources.
- Write outputs back as warehouse tables.
- Emit dbt `sources:` YAML so downstream dbt models can `ref()` judgments.
- Borrow dbt selector syntax and conventions to cut learning curve.

## Scope discipline (non-goals)

No extraction/loading, no general orchestration, no general LLM app framework, no free-text generation. Only: judgments over rows, their tests, their diffs, their review loop.

## Open questions

- Persona: analytics engineer, AI/app engineer, or ops? Online + batch spans two audiences — pick one to lead.
- Multi-question calls: batch all questions of a judgment into one engine call per row (read-once) — yes by default; how to expose speculative fan-out?
- Score questions: how does calibration apply to ordinal scales?
- Order stability costs N× calls; run on a sample only?
- Store format and GC for old versions.
- Trace ingestion format for evals (OTel GenAI semantic conventions?).
- PyPI name (see README).
- Judgment → rule demotion (from Roast's "AI as placeholder" idea, see 03 related work): if a deterministic rule matches the judgment on ≥X% of gold rows, suggest replacing it (`hunch suggest-rules`)?

## First prototype (throwaway)

1. One YAML judgment over a CSV loaded in DuckDB.
2. Content-addressed cache in DuckDB.
3. Change question wording → `diff` prints flipped rows with before/after probabilities.

Success criterion: the diff feels magical.
