# hunch

**dbt for judgments.** Declarative, tested, versioned semantic decisions — powered by System One models like Jev.

> Status: idea / design stage (2026-09-24). No code yet. Everything here came out of one design conversation; see `docs/`.

## The one-paragraph pitch

Companies now classify tickets, transactions, companies, documents, leads, and LLM traces with ad-hoc LLM scripts: prompts in Python strings, no versioning of labels, no diff when a prompt changes, no regression tests, no lineage, no cost control. That is analytics SQL in 2015. `hunch` does for *judgments* what dbt did for SQL transforms: a narrow declarative primitive (a question with a menu of answers over some state), a dependency graph, tests, CI with backtest diffs, and a human review loop — compiled to a cheap calibrated judgment engine (Jev first, pluggable).

## Core ideas

1. **A judgment model** = state (a query/ref over rows, traces, files) + questions (`choice` / `score` / `noul`) with options you define. Output: labels + calibrated probabilities.
2. **Content-addressed results.** Every result is keyed by `hash(state + question + options + engine version)`. From that one primitive: incremental runs, backtest diffs, reproducibility, audit, online cache, review write-back.
3. **Tests derived from how judgment engines fail**: gold-set accuracy, calibration error, option-order stability, complementary-question consistency.
4. **Escalation as a materialization.** Below-threshold rows land in a review queue; human answers flow back as gold data.
5. **Define once, run online and batch.** Same spec runs per-request in an app and as a backfill over a table.
6. **Evals are the same thing.** Production traces are rows; eval assertions are judgments.

## Docs

| Doc | What |
|---|---|
| [01-jev.md](docs/01-jev.md) | What Jev / System One models are, economics, limits |
| [02-dbt.md](docs/02-dbt.md) | Why dbt won: patterns, extension points, lessons to steal |
| [03-opportunity-scan.md](docs/03-opportunity-scan.md) | Which domains are "pre-2016 dbt"; ranked candidates; why hunch |
| [04-design.md](docs/04-design.md) | Clean-slate rationale, architecture, spec sketch, tests, open questions |
| [05-licensing.md](docs/05-licensing.md) | Split-by-layer license model (Apache 2.0 core + ELv2 server) |
| [06-roadmap.md](docs/06-roadmap.md) | Draft roadmap, ordered by risk: trust the numbers, the graph, engines and scale, package, server |

## Decisions so far

| Date | Decision | Why |
|---|---|---|
| 2026-09-24 | Name: `hunch` | System One = gut; a hunch is a calibrated gut call |
| 2026-09-24 | Clean slate, not a dbt package/adapter | Per-row network calls, row-level cache/provenance, human loop, statistical tests, online serving don't fit dbt's model (04-design) |
| 2026-09-24 | dbt interop, not dependency | Read `manifest.json`, write tables back, emit dbt source YAML |
| 2026-09-24 | License split by layer: spec+compiler+CLI Apache 2.0; server ELv2 | Spec adoption needs permissive; protect what a cloud would host (05-licensing) |
| 2026-09-24 | Engine pluggable, Jev first | Avoid single-vendor dependency |
| 2026-09-24 | First user: AI engineer (evals over agent and LLM traces), then analytics engineers, then ops | Strongest evidence so far (SWE-agent: 39% of claimed fixes fail) and the most acute pain (06-roadmap) |

## Status

A package (`src/hunch`, v0.1, not published) grown out of the prototype, plus a server (`server/`, ELv2). The prototype's examples, review panels and results stay in [`prototype/`](prototype/README.md); `prototype/hunch.py` is a shim over the package, so every documented command still runs.

```sh
uv tool install .                   # or: uvx --from . hunch …
hunch init agent-eval my_eval       # a recipe to adapt
hunch compile my_eval               # what it will ask and cost
hunch run my_eval --max-cost 1      # ask what the store lacks, materialize tables (with lineage)
hunch test my_eval                  # accuracy / calibration / AUROC / dial against gold
hunch diff my_eval --against git:HEAD      # or --model deepseek:deepseek-flash: another engine, row by row
hunch review my_eval                # the queue that turns disagreements into gold
hunch suggest my_eval --question claim     # rewrites kept only if they win on held-out gold
```

Library: `hunch.judge(spec, **row)`, `hunch.run(spec)`, `hunch.results(spec)`, and Pydantic classes (or Pydantic AI output types) as specs: `hunch.spec_from_model(Cls, …)`, `hunch.judge_model(Cls, spec, **row)`. Engines: TypeSafe's Jev, or an LLM through answer-token logprobs (`deepseek:…`, `openrouter:…`). Sources: CSV, agent traces (`traces(<glob>)`: Claude Code, Cursor, OpenCode, OpenTelemetry GenAI), or any Python function (`py(file:fn)`, e.g. a dlt resource).

Validated on BANKING77 (intent, flat and tree), SWE-agent runs, real Claude Code conversations, and a 100k-review scale test; every headline number was checked by a blind review panel or an adversarial review. Findings by round: [docs/04-design.md](docs/04-design.md).

## Name availability (checked 2026-09-24)

- PyPI `hunch`: squatted placeholder (0.0.0, "Coming soon..."). Need an alternative dist name (e.g. `hunch-ai`, `hunchdb`) or a PEP 541 claim; CLI command can still be `hunch`.
- npm `hunch`: taken (Markdown search tool, 0.16.0).

## License

Everything in this repository is licensed under the [Apache License 2.0](LICENSE), except third-party data listed in [NOTICE](NOTICE).

Split by layer (see [docs/05-licensing.md](docs/05-licensing.md)): the spec, engine, CLI, library and adapters are Apache 2.0. [`server/`](server/README.md) (online serving, the review UI, runs and drift) is under the Elastic License 2.0 and carries its own [`LICENSE`](server/LICENSE).
