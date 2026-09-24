# hunch

**dbt for judgments.** Declarative, tested, versioned semantic decisions — powered by System One models like Jev.

> Status (2026-09-24): v0.1 package, CLI and a local server, tested on real data; no outside users yet. See [Status](#status) and `docs/`.

## The one-paragraph pitch

Companies now classify tickets, transactions, companies, documents, leads, and LLM traces with ad-hoc LLM scripts: prompts in Python strings, no versioning of labels, no diff when a prompt changes, no regression tests, no lineage, no cost control. That is analytics SQL in 2015. `hunch` does for *judgments* what dbt did for SQL transforms: a narrow declarative primitive (a question with a menu of answers over some state), a dependency graph, tests, CI with backtest diffs, and a human review loop — compiled to a cheap calibrated judgment engine (Jev first, pluggable).

## Explained simply

### What dbt solved

Companies keep data in big tables, and analysts write SQL to turn raw tables into useful ones ("revenue per customer per month"). Before dbt (around 2016) that SQL lived everywhere: dashboards, scripts, laptops. Nobody knew which query fed which number; someone changed a query and a number on the CEO's dashboard quietly broke.

dbt said: **treat those queries like software.**

```
before dbt                          with dbt
─────────────────────────           ─────────────────────────────────
SQL scattered, copy-pasted          one file per table, in git
"which query made this?"            a dependency graph (ref)
changes break things silently       tests run on every change
nobody reviews SQL                  pull requests, code review
```

It invented nothing new. It gave SQL writers the habits software engineers already had: files, version control, tests, review.

**dlt** did the same for *loading* data (pull from an API, put it in a database): one Python function, resumes after failures, records where each row came from. The lessons we took: meet people in the code they already write, and keep lineage.

### Our problem

Software now asks an AI many small questions: is this ticket about billing? did the coding agent actually fix the bug? is this customer angry? Each one is a **judgment**, and judgments live the way SQL did before dbt:

```
today                                      the pain
────────────────────────────────────       ──────────────────────────────────
a prompt pasted inside app code            nobody knows it exists
someone tweaks the wording                 better or worse? no idea
"the AI says 0.9 confident"                is 0.9 right 90% of the time?
run it again on the same data              pay again, slightly different answers
the AI is wrong on some rows               nobody looks at them systematically
```

### What hunch does

The same move as dbt: **treat judgments like code.** A judgment is a small file: the question, the input columns, the possible answers, and (if you have them) the correct answers to test against. Then hunch gives you the habits:

| | in plain words |
|---|---|
| **compile** | "this will ask 2,000 questions and cost $0.08": you know before you pay |
| **run** | asks only what it hasn't asked before; every answer is filed under a fingerprint of its input, so nothing is paid for twice |
| **test** | how often it's right, and whether "90% sure" really means right 90% of the time |
| **the dial** | "act automatically when ≥ 90% sure: that handles 76% of rows at 0.5% mistakes"; you choose how much to trust it by looking |
| **diff** | reworded the question? which rows changed, which got fixed, which broke, and whether it's real or luck |
| **review** | humans see only the rows worth checking (disagreements plus a random sample), and their answers become the answer key |
| **shadow** | try a new version on live traffic, invisibly, then compare |
| **suggest** | an AI proposes better wording; kept only if it wins on data it never saw |

### Why someone would use it

An AI engineer asking "did my agent really do the job?" over thousands of traces:

1. **Stops guessing**: "the new prompt is better" becomes "fixed 36, broke 16, not luck".
2. **Stops paying twice**: re-running 100,000 answered rows takes 4 seconds and costs nothing.
3. **Automates safely**: the dial says how much can run without a human, at what error rate.
4. **Catches false claims**: on real runs, 41% of agents that said "I fixed it" had failing tests, found without running a single test.
5. **Can switch engines**: Jev or an LLM with the same files, and the tests say which is better per task.

**dbt made SQL trustworthy; hunch makes AI judgments trustworthy.**

### CLI, library and server

```
CLI (for the engineer)                    server (for the team)
──────────────────────────────            ──────────────────────────────────
hunch init agent-eval my_eval             POST /v1/judge  → your app asks live
hunch compile my_eval   (cost)            /review         → reviewers click verdicts
hunch run my_eval       (ask)             /runs           → history, cost, drift alerts
hunch test / diff / review / suggest
```

Plus a Python library (`hunch.judge(...)`, Pydantic classes as specs). All three read the same files and the same stored answers: an answer your app got live is free for tomorrow's batch, and a reviewer's click becomes gold for `hunch test`. CLI and library: Apache 2.0. Server: Elastic License (free to self-host, not to resell as a hosted service).

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
