---
name: hunch
description: Write, run, test and change hunch specs, the YAML files that define the judgments a program asks a model to make (classify, route, flag, score) and measure how often the answers are right. Use when a folder has hunch specs or .hunch/, when the user mentions hunch, or when they want to measure, test in CI, review or safely change an LLM classifier, router, grader or guard.
---

# hunch

A spec (YAML) names the rows, what the model sees of each row, and the questions it answers. hunch caches every answer under its exact input, measures accuracy against gold (an answer key or a person's reviews) as a range, and shows what a change flips before it ships.

Docs index: https://fuguai.mintlify.site/llms.txt. Add `.md` to any page URL for Markdown. The pages to read first: `reference/spec.md` (every key), `reference/cli.md` (commands, flags, exit codes), `guides/write-a-spec.md`.

## Rules

1. **Never spend without a cap.** Every command except `lint`, `compile`, `init`, `skill`, `docs` and `distill` can ask the engine for missing answers, including `review --list`. Give each one `--max-cost USD`. Start with `--max-cost 0`: it answers from the cache and stops before asking if anything is missing. Raise it only to what `compile` estimated, and only after the user agrees to that amount; "run it" said before they knew the cost is not agreement. Also `export HUNCH_MAX_COST=0` at the start of a session: a command you forget to cap then stops instead of spending, and `--max-cost` still overrides it.
2. **Never write verdicts.** `*.reviews.csv` is gold, a person's judgment. Do not create or edit it, and do not answer `hunch review` prompts. Show the queue with `hunch review PATH --list --max-cost 0` and let the person review. The one exception: the user explicitly asks you to review. Then say first that your verdicts are not human gold, and record them only under a name that says so (`--reviewer ai-agent`), never theirs.
3. **Do not touch `.hunch/`.** `.hunch/store.sqlite` is a cache that hunch rebuilds; `.hunch/target/` holds results. Read them; never edit them.
4. **Measure before and after a change.** Commit the spec before editing it, so `diff --against git:HEAD` has the old version (without git, copy the spec first and pass the copy: `--against old/command_guard.yml`). Run `test` before, `diff` after, and report the rows it flips, not only the new accuracy. A new or reworded question has no cached answers, so its `diff` costs what `compile` shows.
5. **Commit** the specs and `*.reviews.csv`, never `.hunch/`.

## The loop

```bash
hunch lint PATH                             # spec errors; exit 2 if any
hunch compile PATH                          # prints the request it would send, rows, cost; sends nothing
hunch run PATH --sample 50 --max-cost 0.01  # try on 50 rows first; the same 50 every time
hunch run PATH --max-cost <from compile>
hunch test PATH --max-cost 0                # accuracy with a range, dial, calibration, checks
hunch review PATH --list --max-cost 0      # rows a person should judge next
hunch diff PATH --against git:HEAD --max-cost 0.01
```

`PATH` is a spec file or a folder of specs (a project). `--node NAME` picks one judgment in a project.

After `test`, read `.hunch/target/<tested path>.json` rather than parsing the terminal: per question the accuracy estimate and its interval, each check with pass or fail, metrics, examples, cost and git sha. Exit codes: 0 done (for `test`, every check passed); 1 a `FAIL`, or stopped with a message such as the cost cap (read the last line to tell which); 2 lint error or bad arguments.

`hunch docs PATH` writes a page people can read and search (each judgment's status, numbers and lineage) to `.hunch/target/`; it asks nothing. Name what uses a judgment under `exposures:` so `diff` can say what a change affects; when app code branches on particular answers, write `uses: {department: [billing]}` so renaming one fails lint. If lint says an exposure relies on an answer that is gone, the app needs changing first: tell the user, don't just edit `uses`.

To start from a question instead of YAML: `hunch ask "Would this command destroy data?" rows.csv --max-cost 0.01` (`--options a,b,c` for one of several answers, `--columns` for what the model sees). It writes `<name>.yml` and `<name>.answers.csv` and prints the rows it was least sure of; from there the spec goes through the loop above like any other.

## Batteries: start from a measured decision

Before writing a spec, check whether a battery already makes the decision: `hunch init --list`, then `hunch init NAME DIR` (DIR must not exist yet). Each ships its spec, the labelled rows it was measured on, and `results.json`: what it measured (compare the user's `hunch test` against it, not against a guess).

| Battery | Reach for it when | Measured |
|---|---|---|
| `agent-commands` | a coding agent's shell commands need a person's look before they run (destroys, reaches outside the project, sends data out) | `destroys` 97.4% on 38 real agent commands; live use is `hunch hook install` (the user's call) |
| `rag-answers` | an app shows answers written from retrieved passages: does the answer say anything they don't support? | AUROC 0.941 on 900 annotated RAGTruth answers; acting on "no" at 0.7 passes 54% of answers with 0.8% wrong, the rest go to a person |
| `agent-eval` | judge a coding agent's claimed fixes from its own traces (bring the traces) | none shipped: no public labelled traces |
| `tickets` | a teaching example: choice, yes/no and score on 40 made-up tickets | not evidence of anything real |

After swapping in the user's rows, the battery's numbers no longer apply: review a few dozen rows (`hunch review`) and `hunch test` again before anything acts on it. `hunch hook install` is the user's call, not yours: it changes how their agent runs commands. Suggest it; don't run it unasked.

## Writing a spec

- One narrow judgment per question. The question id is for code; the model sees only `instructions` and `criteria`, so put the whole meaning there.
- `state` lists only the columns the model needs. `redact` removes secrets and personal data before anything is sent; `clip` bounds long text.
- Types: `choice` (one option; describe every option in `criteria`), `noul` (yes or no; optional criteria under the keys `"true"` and `"false"`), `score` (ordered levels, lowest first), `multi` (several options can apply).
- Add `none:` to a `choice` when no option may fit, so declining is an answer rather than low confidence.
- `gold:` names the answer-key column; empty cells are fine. Without an answer key, reviews become the gold.
- `act:` is the confidence at or above which code acts on an answer. Set it from the dial that `test` prints, not by guessing.
- `tests:` thresholds fail the build; `severity: warn` reports without failing. `examples:` pins known rows that must get a given answer. `metrics:` measures a rule over answers (for example "any of these questions says yes").
- Specs whose first line is `# yaml-language-server: $schema=https://raw.githubusercontent.com/oneryalcin/hunch/main/src/hunch/spec.schema.json` get completion and validation in the editor.

A judgment can read another's answers with `source: ref(other)`, and `where:` limits which rows it asks. See `reference/projects.md`.

## Engines and keys

`model: jev-…` needs `TYPESAFE_API_KEY`; `deepseek:<id>` needs `DEEPSEEK_API_KEY`; `openrouter:<id>` needs `OPENROUTER_API_KEY`. `--model ID` runs the same specs on another engine into separate tables, and `diff --model ID` compares engines. If a key is missing, say so; do not look for one.

## In application code

```python
import hunch
answers = hunch.judge("hunch/command_guard.yml", request=..., cwd=..., description=..., command=...)  # ajudge in an event loop
if answers["destroys"]["label"] == "yes" or answers["destroys"]["route"] == "review":
    ...  # hold the command for a person
```

The same store serves batch runs, SQL and the app, so a row seen in any of them costs nothing the second time. `HUNCH_MAX_COST` caps spend in a service.

In SQL (the `sql` extra): `hunch.sql.register(con, "hunch/command_guard.yml", max_cost=0.05)` adds a DuckDB function named after the judgment; `command_guard(request, cwd, description, command).destroys.label` is its answer for a row. `max_cost` is a total for everything the function asks. Read CSVs with `all_varchar = true`. In dbt-duckdb, the plugin `module: hunch.dbt` with `config: {specs: [...], max_cost: USD}` does the same; materialize such models as tables. See `guides/sql.md`.

## A local model

`hunch distill PATH --source rows.csv` (the `distill` extra) trains a small local model per question on the answers already in the store; it asks nothing. Use it as `model: distilled:<folder>` with `escalate: {model: <the original engine>}` and `act` from the dial, so what it isn't sure of goes to the engine. Measure it first (`hunch test PATH --model distilled:<folder> --max-cost 0`), and don't use it for a rare answer that is costly to miss (it warns under 30 examples). See `guides/distill.md`.
