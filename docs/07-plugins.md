# Plugins and extension boundaries

Decision recorded 26 September 2026: **engines are the only code plugin type**. Read rows through `py()` or dlt, and write results through existing data tools; hunch has no source or sink plugin API. This supersedes the broader plugin proposal in the [roadmap](06-roadmap.md). The [spec reference](../docs-site/reference/spec.mdx) and [engine reference](../docs-site/reference/engines.mdx) describe current usage.

The rest of this note explains the decision, the evidence from dbt and other tools, and what would justify adding another extension point. Two independent reviews and a fact check shaped it; their changes are recorded at the end.

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

## What shipped, and what would come next

| Status | Extension work | Evidence or trigger |
|---|---|---|
| Done, 26 September | Batteries with receipts | `rag-answers` ships all 900 measured rows; `hunch test --receipt` writes `results.json` beside a battery. The agent skill lists available batteries. |
| Done, 26 September | Versioned artifacts | `results.schema.json` and `manifest.schema.json` are checked in CI against real files. CI catches stale battery receipts; `results_comment.py` shows one consumer. The file contract is in the [file reference](../docs-site/reference/files.mdx). |
| Done, 26 September | Benchmark from receipts | `docs-site/benchmark.py` generates the benchmark page; CI checks it against the receipts. |
| Done, 26 September | Engine plugin example | The Ollama engine lives in its own package under `plugins/`, with native prompts and CI smoke tests. A plugin without `adapter` uses its package version in cache keys so a changed prompt does not reuse an old answer. |
| When requested | Install batteries from git | Add `hunch add <git url>` when someone needs a battery that is not bundled; consider a hub after a second author publishes one. |
| When needed | Broaden engine and source contracts | Add an engine contract check for a second outside engine author, batch answers for a local engine that is too slow row by row, or a trace-reader entry point for a third outside format. Add a dlt adapter when a data-pipeline user needs it. |

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
