# Plugins: what they solve, who writes them, what hunch needs (2026-09-26)

Status: decided 2026-09-26, after two independent answers and an adversarial review with a fact check (below).
Supersedes the "engine, source and sink plugins through Python entry points" step in 06-roadmap's order: engines
are the only code plugin; sources go through dlt and `py()`; there are no sink plugins.
The first version was written in conversation; two agents then answered the same question without reading it
(one from dbt's history, one as a sceptic comparing pytest, Great Expectations, dlt, LangChain, OpenTelemetry and
Terraform). Where all three agreed, the text stands; where the reviews were better, it changed, and "What the
reviews changed" says how.

## What hunch is, and where its parts join

hunch turns a question into a decision your software can act on, and tells you how often it's right. Each
decision passes through the same stages, and each stage is a place something outside hunch could plug in:

```
rows come in ─► what the model sees ─► a model answers ─► answers are kept ─► software acts, or a person looks
   (sources)       (redaction)          (engines)          (stores)          (runtimes, reviewers)
                                                                                     │
          the question itself (batteries) ◄── measured against people's verdicts ◄──┘
                                              (tests, results files)
```

## What plugins did for dbt

dbt had four kinds of extension, and they grew because each gave someone outside dbt Labs a reason to build:

1. **Adapters.** dbt Labs didn't write support for every warehouse; Databricks wrote and owns its adapter, and the
   DuckDB one began as a community project (now under duckdb/dbt-duckdb). Vendors are listed as trusted adapters,
   which gives them a reason to keep theirs current.
2. **Packages (dbt-utils, dbt-expectations, Fivetran's).** Knowledge became installable. Fivetran shipped ready-made
   models for the data its connectors load, which made its own product more useful. Community packages spread good
   practice.
3. **Artifacts.** dbt writes machine-readable files describing every project and run (`manifest.json`,
   `run_results.json`), and whole companies built on them without asking permission: Datafold uploads
   `manifest.json`, Lightdash reads it, Elementary collects artifacts and test results through a dbt package. That
   wasn't a plugin API, just an open, stable file format, and it did as much as the plugins. When dbt rewrote its
   engine (Fusion), the open adapter API was dropped for adapters dbt Labs signs; the artifacts carried over.
4. **Runtimes.** Airflow, Dagster and the dbt-duckdb plugin run dbt inside other tools. This is dbt plugging into
   others, not the other way round.

What they share: **someone outside the company had a reason to build it.** A plugin type nobody has a reason to
write stays empty, however good the interface. And it came after dbt had users: today every hunch author is us,
and the only real candidate for a second engine is a decision-model maker such as the GLiNER2.5-Decide team. So
nothing below is built for a stranger until one appears; what is built now must pay off for us first.

## Plugins hunch could have, judged by who has a reason to write them

| Plugin | What it solves | Who has a reason to write it | Worth it? |
|---|---|---|---|
| **Engines** (done) | Compare any model on your own data | **Model makers.** Their model shows up measured on customers' real work, the way a warehouse wanted a dbt adapter. | High: this is how hunch becomes the neutral place to choose a model |
| **Batteries as packages** | A decision someone already worked out, with its evidence | **Domain experts and companies selling a product.** A support tool ships "does this ticket need a human?" tuned to its own data, as Fivetran shipped models for its data. | Highest, see below |
| **Agent readers and hooks** | New coding agents keep appearing (Gemini CLI, Aider…) | Mostly us, for now. The agents' users would, once hunch has users | **Core, not a plugin.** One reader and one hook table row each, written by us; an entry point only when a third outside format is asked for |
| **Sources** | Rows from Slack, Zendesk, S3, warehouses | Mostly already built: **dlt** connects to hundreds of systems | One dlt adapter, not a plugin type of our own |
| **Stores** | A cache shared by several workers or a whole team | Infrastructure companies, later | Build Postgres ourselves when needed; no plugin type |
| **Reviewers** | Where people give their verdicts: Slack approval buttons, labelling tools like Label Studio or Argilla, PR comments | Nobody soon | **A verdict file format, not a plugin:** document and version the `*.reviews.csv` that exists (JSONL only if a labelling tool needs it). People won't invest in reviews they can't take with them |
| **Checks** | Tests beyond accuracy: fairness across groups, cost per decision, speed | Very few | **No.** New statistics fragment the one thing everyone must trust; the `metrics:` rule language covers domain rules |
| **Redaction rules** | Domain rules for personal data (health records, card data) | Compliance teams | Low; regex rules in the spec cover it |

## The two that matter most

**1. Batteries as packages: installable decisions that come with their evidence.**
A prompt library gives you words. A hunch battery gives you:
- the question;
- labelled rows it was measured on;
- its tests;
- its measured score (rag-answers: AUROC 0.941 on RAGTruth's 900 marked answers; the battery ships 40 of them,
  0.918, and should ship all 900, which the MIT licence allows: a sample is a demo, not a benchmark).

This is what serves the community:
- **Every battery is a benchmark.** With engine plugins, anyone can ask "which model is best at checking whether an
  answer is supported?" on shared, labelled data. That's the neutral-judge idea made concrete.
- **Improvements come with proof.** Someone proposes better wording, `hunch diff` shows which rows it fixes and
  breaks, and the change is accepted on evidence. Prompt libraries can't do that.
- **Verdicts add up.** Each person who reviews rows adds to the answer key, so the battery gets better measured over
  time. Only public data can be pooled, never a company's own.
- **Vendors have a reason to publish.** "Here is our churn battery, 94% on 2,000 labelled calls" sells their product.

**2. The artifacts as an open contract.** (All three answers put this in their top three.)
This is dbt's quiet lesson. hunch already writes:
- `results.json` (every test's numbers);
- `*.reviews.csv` (people's verdicts);
- the store's tables.

If their formats are documented, versioned and kept stable, others can build dashboards, CI bots, catalogs and
alerting on top without any plugin API. This is cheap and needs no permission from us.

## What not to do

- **No marketplace or registry before there are authors.** Installing a battery from a git URL (`hunch add`,
  where `hunch init` copies a bundled one) waits for someone to ask; a hub waits for a second author.
- **No plugin type we'd be the only one to write.** Stores, for example.
- **No new format where one exists.** dlt for sources, OpenTelemetry for traces, dbt for warehouses.
- **Be clear about trust.** A plugin runs code on your machine, and an engine plugin receives your rows. `compile`
  already shows what is sent; it should also name the engine it goes to. A battery needs its data's licence and its
  evidence alongside it, or it's just a prompt.
- **No other people's plugins in our repository.** LangChain moved its integrations out of the core package into
  `langchain-community`, then into standalone packages the vendors own, for dependency and version management.
  Even hunch's own Ollama plugin should ship as its own package.
- **Keep the plugin contract tiny.** An interface others implement is the part a rewrite has to break or keep
  (Fusion kept the files, not the adapter API); one async method is small enough to keep.
- **An `act` bar tuned for one engine is wrong for another.** A battery names the built-in engine it was measured
  on, so it reproduces anywhere, and lists the other engines it was measured with.

## Suggested order

1. **Batteries with receipts.** (Done 2026-09-26: `rag-answers` ships all 900 rows; `hunch test --receipt` writes
   `results.json` beside a battery, and the three with data ship one; the skill lists the batteries.) A battery ships all the labelled rows it was measured on (not a sample), its data
   licence, and its results beside the spec (today `hunch test` writes them only under the git-ignored `.hunch/`,
   so a battery-level results path comes first), reproducible with `hunch test`. And it is found by agents: `hunch
   skill` teaches a coding agent the batteries that exist and when to reach for one, since the first users work in
   Claude Code and Codex.
2. (Done 2026-09-26: `results.schema.json` and `manifest.schema.json`, checked in CI against real files; CI
   fails a battery whose receipt is stale; `reference/files` is the contract; `results_comment.py` the example
   consumer.) **Document and version the artifacts** (`results.json`, `manifest.json`, `*.reviews.csv`): a JSON Schema, a
   version field, a changelog and one example consumer (a GitHub Action that comments a spec's numbers on a PR).
3. **The benchmark table,** every battery × every engine, generated from the artifacts, not written by hand.
4. **Engines, only what pays now:** `adapter` defaults to the plugin package's version, so a changed prompt can't
   serve stale cached answers; the Ollama plugin becomes its own package, the reference implementation with a smoke
   test, able to use a model's native prompt. (`compile` already names a plugin engine; answers are already checked
   at fill time.)
5. **Later, with a trigger:** `hunch add <git url>` (someone asks to install a battery that isn't bundled); a hub
   (a second author); `hunch engine check` and a contract version (a second engine author); batch `answer_many`
   (a local engine that is too slow one row at a time); a trace-reader entry point (a third outside format); one
   dlt source adapter (the data-pipeline side needs it).

## What the reviews changed

- Agent hooks and trace readers moved from "plugins" to core: we are the only ones writing them for now (both
  reviews).
- Reviewers became a verdict format, not a plugin type (both); custom checks became "no" (both).
- Order: batteries with receipts first, artifacts second (the dbt-history review's order; the sceptic put engine
  hardening first). All three answers had the same three items in their top three.
- Added: out-of-tree plugins, the per-engine `act` warning, verdicts as a documented format so gold outlives hunch,
  a default `adapter`.
- The adversarial review then fact-checked the ecosystem claims (Databricks, DuckDB, Fivetran, Datafold, Lightdash,
  Elementary confirmed; the Fusion "burden" claim was wrong and is now what the sources say; the LangChain reason
  softened) and cut what builds for authors who don't exist yet: `hunch engine check`, a contract version and
  `answer_many` moved to "with a trigger", and git install waits for someone to ask. It also caught that "committed
  results" had no place to live, that the battery shipped a 40-row sample of a 900-row benchmark, that the record
  said both `hunch add` and `hunch init <git url>`, and one mechanism all three answers missed: batteries found
  through the agent skill.
