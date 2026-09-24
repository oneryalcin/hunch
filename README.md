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

## Decisions so far

| Date | Decision | Why |
|---|---|---|
| 2026-09-24 | Name: `hunch` | System One = gut; a hunch is a calibrated gut call |
| 2026-09-24 | Clean slate, not a dbt package/adapter | Per-row network calls, row-level cache/provenance, human loop, statistical tests, online serving don't fit dbt's model (04-design) |
| 2026-09-24 | dbt interop, not dependency | Read `manifest.json`, write tables back, emit dbt source YAML |
| 2026-09-24 | License split by layer: spec+compiler+CLI Apache 2.0; server ELv2 | Spec adoption needs permissive; protect what a cloud would host (05-licensing) |
| 2026-09-24 | Engine pluggable, Jev first | Avoid single-vendor dependency |

## Status

Throwaway prototype in [`prototype/`](prototype/README.md): content-addressed cache, statistical tests, backtest diff with significance, online `judge()`, validated on BANKING77 (dev + holdout). Findings that shape the real build are in [docs/04-design.md](docs/04-design.md).

## Name availability (checked 2026-09-24)

- PyPI `hunch`: squatted placeholder (0.0.0, "Coming soon..."). Need an alternative dist name (e.g. `hunch-ai`, `hunchdb`) or a PEP 541 claim; CLI command can still be `hunch`.
- npm `hunch`: taken (Markdown search tool, 0.16.0).

## License

Everything in this repository is licensed under the [Apache License 2.0](LICENSE), except third-party data listed in [NOTICE](NOTICE).

Planned split (see [docs/05-licensing.md](docs/05-licensing.md)): the spec, compiler, CLI, SDKs and engine adapters stay Apache 2.0. A future `server/` directory (review UI, online serving, trace ingestion) will be under the Elastic License 2.0 and will carry its own `LICENSE` file. No ELv2 code exists yet.
